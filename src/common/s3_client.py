import uuid
import asyncio
import boto3
import httpx
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse
from botocore.exceptions import ClientError, NoCredentialsError

from src.utils.logger import get_logger
from src.settings import settings

logger = get_logger()


class S3ImageUploader:
    """AWS S3 client for uploading images with proper error handling."""

    def __init__(self):
        """Initialize S3 client with credentials from settings."""
        try:
            self.s3_client = boto3.client(
                "s3",
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                region_name=settings.aws_region,
            )
            self.bucket_name = settings.aws_s3_bucket_name
            # Prefixes to match S3 lifecycle rules (aws/lifecycle.json)
            self.persistent_prefix = "persistent/"
            # Map retention policies to S3 prefixes
            self.retention_prefix_map = {
                "permanent": "persistent/",
                "one_day": "ephemeral/one_day/",
                "one_week": "ephemeral/one_week/",
                "two_weeks": "ephemeral/two_weeks/",
                "one_month": "ephemeral/one_month/",
            }
            # Default ephemeral prefix for backward compatibility
            self.ephemeral_prefix = "ephemeral/one_day/"

            if not self.bucket_name:
                raise ValueError("AWS_S3_BUCKET_NAME not configured")

        except NoCredentialsError:
            logger.error(
                "AWS credentials not found. Please configure AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
            )
            raise
        except Exception as e:
            logger.error(f"Failed to initialize S3 client: {e}")
            raise

    def _get_prefix_from_retention_policy(
        self, retention_policy: Optional[str] = None
    ) -> str:
        """
        Get S3 prefix from retention policy.

        Args:
            retention_policy: Retention policy string ('permanent', 'one_day', 'one_week', 'two_weeks', 'one_month')

        Returns:
            S3 prefix string matching the retention policy
        """
        if retention_policy:
            return self.retention_prefix_map.get(
                retention_policy, self.persistent_prefix
            )
        return self.persistent_prefix

    def _generate_unique_filename(
        self,
        original_url: str,
        persistent: bool = True,
        retention_policy: Optional[str] = None,
    ) -> str:
        """
        Generate a unique filename for the image.

        Args:
            original_url: Original image URL
            persistent: Legacy flag for backward compatibility (True = permanent, False = ephemeral)
            retention_policy: Retention policy string (takes precedence over persistent flag)

        Returns:
            S3 key with proper prefix
        """
        # Extract file extension from URL or default to jpg
        parsed_url = urlparse(original_url)
        path = parsed_url.path.lower()

        if path.endswith((".jpg", ".jpeg")):
            extension = ".jpg"
        elif path.endswith(".png"):
            extension = ".png"
        elif path.endswith(".gif"):
            extension = ".gif"
        elif path.endswith(".webp"):
            extension = ".webp"
        else:
            extension = ".jpg"  # Default to jpg

        # Generate unique filename
        unique_id = str(uuid.uuid4())
        # Use retention_policy if provided, otherwise fall back to persistent flag
        if retention_policy:
            prefix = self._get_prefix_from_retention_policy(retention_policy)
        else:
            prefix = self.persistent_prefix if persistent else self.ephemeral_prefix
        return f"{prefix}facebook_image_{unique_id}{extension}"

    def _get_content_type(self, filename: str) -> str:
        """Get content type based on file extension."""
        if filename.lower().endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        elif filename.lower().endswith(".png"):
            return "image/png"
        elif filename.lower().endswith(".gif"):
            return "image/gif"
        elif filename.lower().endswith(".webp"):
            return "image/webp"
        else:
            return "image/jpeg"  # Default

    def _extract_s3_key_from_url(self, s3_url: str) -> Optional[str]:
        """Extract S3 key from S3 URL."""
        try:
            # Parse S3 URL to extract the key
            # Expected format: https://bucket-name.s3.region.amazonaws.com/key
            parsed_url = urlparse(s3_url)

            # Check if it's a valid S3 URL
            if not parsed_url.hostname or not parsed_url.hostname.endswith(
                ".amazonaws.com"
            ):
                logger.warning(f"URL doesn't appear to be an S3 URL: {s3_url}")
                return None

            # Extract key (path without leading slash)
            key = parsed_url.path.lstrip("/")
            if not key:
                logger.warning(f"No key found in S3 URL: {s3_url}")
                return None

            return key
        except Exception as e:
            logger.error(f"Failed to extract S3 key from URL {s3_url}: {e}")
            return None

    async def copy_to_permanent(self, s3_url: str) -> Optional[str]:
        """
        Copy an ephemeral image to permanent storage, returns new S3 URL.

        Args:
            s3_url: S3 URL of the ephemeral image to copy

        Returns:
            New permanent S3 URL or None if copy fails
        """
        try:
            # Extract S3 key from URL
            source_key = self._extract_s3_key_from_url(s3_url)
            if not source_key:
                logger.error(f"Could not extract S3 key from URL: {s3_url}")
                return None

            # Check if already permanent
            if source_key.startswith(self.persistent_prefix):
                logger.debug(f"Image already in permanent storage: {s3_url}")
                return s3_url

            # Download the image data first (to avoid S3 cross-region issues)
            # Use asyncio.to_thread to avoid blocking the event loop
            try:
                response = await asyncio.to_thread(
                    self.s3_client.get_object,
                    Bucket=self.bucket_name,
                    Key=source_key,
                )
                image_data = await asyncio.to_thread(response["Body"].read)
                content_type = response.get("ContentType", "image/jpeg")
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "Unknown")
                if error_code == "NoSuchKey":
                    logger.error(f"Source image not found in S3: {source_key}")
                    return None
                raise

            # Generate new permanent key
            # Extract file extension from original key
            original_filename = source_key.split("/")[-1]
            file_extension = ""
            if "." in original_filename:
                file_extension = "." + original_filename.rsplit(".", 1)[-1].lower()
                # Normalize extension
                if file_extension in [".jpg", ".jpeg"]:
                    file_extension = ".jpg"
                elif file_extension not in [".png", ".gif", ".webp"]:
                    file_extension = ".jpg"
            else:
                file_extension = ".jpg"

            unique_id = str(uuid.uuid4())
            destination_key = f"{self.persistent_prefix}{unique_id}{file_extension}"

            # Upload to permanent location (offload to thread)
            await asyncio.to_thread(
                self.s3_client.put_object,
                Bucket=self.bucket_name,
                Key=destination_key,
                Body=image_data,
                ContentType=content_type,
                CacheControl="max-age=31536000",  # Cache for 1 year
            )

            # Generate new permanent S3 URL
            permanent_url = f"https://{self.bucket_name}.s3.{settings.aws_region}.amazonaws.com/{destination_key}"

            logger.info(
                f"Copied image from ephemeral {source_key} to permanent {destination_key}"
            )
            return permanent_url

        except ClientError as e:
            logger.error(f"AWS S3 copy failed for {s3_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during image copy {s3_url}: {e}")
            return None

    async def copy_to_retention(
        self, s3_url: str, target_retention: str
    ) -> Optional[str]:
        """
        Copy S3 file to a different retention prefix. Returns new S3 URL.

        Args:
            s3_url: S3 URL of the image to copy
            target_retention: Target retention policy ('one_day', 'one_week', 'two_weeks', 'one_month', 'permanent')

        Returns:
            New S3 URL in target retention tier or None if copy fails
        """
        try:
            # Extract S3 key from URL
            source_key = self._extract_s3_key_from_url(s3_url)
            if not source_key:
                logger.error(f"Could not extract S3 key from URL: {s3_url}")
                return None

            # Get target prefix
            target_prefix = self._get_prefix_from_retention_policy(target_retention)

            # Check if already in target prefix
            if source_key.startswith(target_prefix):
                logger.debug(
                    f"Image already in target retention {target_retention}: {s3_url}"
                )
                return s3_url

            # Download the image data (offload to thread to avoid blocking event loop)
            try:
                response = await asyncio.to_thread(
                    self.s3_client.get_object,
                    Bucket=self.bucket_name,
                    Key=source_key,
                )
                image_data = await asyncio.to_thread(response["Body"].read)
                content_type = response.get("ContentType", "image/jpeg")
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "Unknown")
                if error_code == "NoSuchKey":
                    logger.error(f"Source image not found in S3: {source_key}")
                    return None
                raise

            # Generate new key in target prefix
            # Extract file extension from original key
            original_filename = source_key.split("/")[-1]
            file_extension = ".jpg"
            if "." in original_filename:
                ext = "." + original_filename.rsplit(".", 1)[-1].lower()
                if ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                    file_extension = ".jpg" if ext in [".jpg", ".jpeg"] else ext

            unique_id = str(uuid.uuid4())
            destination_key = f"{target_prefix}{unique_id}{file_extension}"

            # Determine cache control based on retention policy
            cache_map = {
                "permanent": "max-age=31536000",  # 1 year
                "one_month": "max-age=2592000",  # 30 days
                "two_weeks": "max-age=1209600",  # 14 days
                "one_week": "max-age=604800",  # 7 days
                "one_day": "max-age=86400",  # 1 day
            }
            cache_control = cache_map.get(target_retention, "max-age=86400")

            # Upload to new location (offload to thread)
            await asyncio.to_thread(
                self.s3_client.put_object,
                Bucket=self.bucket_name,
                Key=destination_key,
                Body=image_data,
                ContentType=content_type,
                CacheControl=cache_control,
            )

            # Generate new S3 URL
            new_url = f"https://{self.bucket_name}.s3.{settings.aws_region}.amazonaws.com/{destination_key}"

            logger.info(
                f"Copied image from {source_key} to {destination_key} (retention: {target_retention})"
            )
            return new_url

        except ClientError as e:
            logger.error(
                f"AWS S3 copy_to_retention failed for {s3_url} to {target_retention}: {e}"
            )
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error in copy_to_retention {s3_url} to {target_retention}: {e}"
            )
            return None

    async def delete_image_from_url(self, s3_url: str) -> bool:
        """
        Delete an image from S3 using its URL.

        Args:
            s3_url: S3 URL of the image to delete

        Returns:
            True if deletion was successful, False otherwise
        """
        try:
            # Extract S3 key from URL
            s3_key = self._extract_s3_key_from_url(s3_url)
            if not s3_key:
                logger.error(f"Could not extract S3 key from URL: {s3_url}")
                return False

            # Delete the object from S3 (offload to thread)
            await asyncio.to_thread(
                self.s3_client.delete_object,
                Bucket=self.bucket_name,
                Key=s3_key,
            )

            return True

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "NoSuchKey":
                logger.warning(f"Image not found in S3 (already deleted?): {s3_url}")
                return True  # Consider it successful if already deleted
            else:
                logger.error(f"AWS S3 delete failed for {s3_url}: {e}")
                return False
        except Exception as e:
            logger.error(f"Unexpected error during image deletion {s3_url}: {e}")
            return False

    async def batch_delete_images_from_urls(self, s3_urls: List[str]) -> List[bool]:
        """
        Delete multiple images from S3 concurrently.

        Args:
            s3_urls: List of S3 URLs to delete

        Returns:
            List of boolean results for each deletion attempt
        """
        if not s3_urls:
            return []

        logger.info(f"Starting batch deletion of {len(s3_urls)} images from S3")

        # Use semaphore to limit concurrent deletions
        semaphore = asyncio.Semaphore(10)  # Max 10 concurrent deletions

        async def delete_with_semaphore(url: str) -> bool:
            async with semaphore:
                return await self.delete_image_from_url(url)

        # Execute all deletions concurrently
        tasks = [delete_with_semaphore(url) for url in s3_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and handle any exceptions
        deletion_results = []
        successful_deletions = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Exception during deletion of image {i+1}: {result}")
                deletion_results.append(False)
            elif result:
                deletion_results.append(True)
                successful_deletions += 1
            else:
                deletion_results.append(False)

        logger.info(
            f"Batch deletion completed: {successful_deletions}/{len(s3_urls)} images deleted successfully"
        )
        return deletion_results

    async def upload_image_from_url(
        self,
        image_url: str,
        *,
        persistent: bool = True,
        retention_policy: Optional[str] = None,
    ) -> Optional[str]:
        """
        Download an image from URL and upload it to S3.

        Args:
            image_url: URL of the image to download and upload
            persistent: Legacy flag for backward compatibility (True = permanent, False = ephemeral)
            retention_policy: Retention policy string ('permanent', 'one_day', 'one_week', 'two_weeks', 'one_month')
                             Takes precedence over persistent flag

        Returns:
            S3 URL of the uploaded image or None if upload fails
        """
        try:
            # Download image from URL
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(image_url)
                response.raise_for_status()

                image_data = response.content

                if len(image_data) == 0:
                    logger.error("Downloaded image is empty")
                    return None

                # Generate unique filename with proper prefix based on retention policy
                filename = self._generate_unique_filename(
                    image_url, persistent=persistent, retention_policy=retention_policy
                )
                content_type = self._get_content_type(filename)

                # Determine cache control based on retention policy
                if retention_policy == "permanent" or (
                    not retention_policy and persistent
                ):
                    cache_control = "max-age=31536000"  # Cache for 1 year
                elif retention_policy == "one_week":
                    cache_control = "max-age=604800"  # Cache for 7 days
                elif retention_policy == "two_weeks":
                    cache_control = "max-age=1209600"  # Cache for 14 days
                elif retention_policy == "one_month":
                    cache_control = "max-age=2592000"  # Cache for 30 days
                else:  # one_day or default ephemeral
                    cache_control = "max-age=86400"  # Cache for 1 day

                # Upload to S3 (offload to thread to avoid blocking event loop)
                await asyncio.to_thread(
                    self.s3_client.put_object,
                    Bucket=self.bucket_name,
                    Key=filename,
                    Body=image_data,
                    ContentType=content_type,
                    CacheControl=cache_control,
                )

                # Generate public S3 URL
                s3_url = f"https://{self.bucket_name}.s3.{settings.aws_region}.amazonaws.com/{filename}"

                return s3_url

        except httpx.HTTPError as e:
            logger.error(f"Failed to download image from {image_url}: {e}")
            return None
        except ClientError as e:
            logger.error(f"AWS S3 upload failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during image upload: {e}")
            return None

    async def batch_upload_images_from_urls(
        self, image_urls: List[str]
    ) -> List[Optional[str]]:
        """
        Download and upload multiple images concurrently for better performance.

        Args:
            image_urls: List of image URLs to download and upload

        Returns:
            List of S3 URLs for successfully uploaded images (None for failures)
        """
        if not image_urls:
            return []

        logger.info(f"Starting batch upload of {len(image_urls)} images")

        # Use semaphore to limit concurrent uploads (avoid overwhelming S3 or network)
        semaphore = asyncio.Semaphore(10)  # Max 10 concurrent uploads

        async def upload_with_semaphore(url: str) -> Optional[str]:
            async with semaphore:
                return await self.upload_image_from_url(url)

        # Execute all uploads concurrently
        tasks = [upload_with_semaphore(url) for url in image_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and handle any exceptions
        upload_results = []
        successful_uploads = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Exception during upload of image {i+1}: {result}")
                upload_results.append(None)
            elif result is None:
                logger.warning(f"Failed to upload image {i+1}")
                upload_results.append(None)
            else:
                upload_results.append(result)
                successful_uploads += 1

        logger.info(
            f"Batch upload completed: {successful_uploads}/{len(image_urls)} images uploaded successfully"
        )
        return upload_results

    async def batch_check_files_exist(self, s3_urls: List[str]) -> Dict[str, bool]:
        """Check if multiple S3 files actually exist using HEAD requests (concurrent).

        Uses asyncio.to_thread so boto3 sync calls don't block the event loop.

        Args:
            s3_urls: List of S3 URLs to check.

        Returns:
            Dict mapping each s3_url → True (file exists) or False (missing/error).
        """
        if not s3_urls:
            return {}

        semaphore = asyncio.Semaphore(10)

        async def _check_one(url: str) -> Tuple[str, bool]:
            async with semaphore:
                s3_key = self._extract_s3_key_from_url(url)
                if not s3_key:
                    return (url, False)
                try:
                    await asyncio.to_thread(
                        self.s3_client.head_object,
                        Bucket=self.bucket_name,
                        Key=s3_key,
                    )
                    return (url, True)
                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "")
                    if error_code in ("404", "NoSuchKey"):
                        return (url, False)
                    logger.warning("S3 HEAD check failed for %s: %s", url, e)
                    return (url, False)
                except Exception as e:
                    logger.warning("Unexpected error checking S3 file %s: %s", url, e)
                    return (url, False)

        tasks = [_check_one(url) for url in s3_urls]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: Dict[str, bool] = {}
        for i, result in enumerate(raw_results):
            if isinstance(result, Exception):
                logger.error(
                    "Exception in batch S3 check for %s: %s", s3_urls[i], result
                )
                results[s3_urls[i]] = False
            else:
                url, exists = result
                results[url] = exists

        return results

    def test_connection(self) -> bool:
        """Test S3 connection and bucket access."""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"S3 connection test successful for bucket: {self.bucket_name}")
            return True
        except ClientError as e:
            logger.error(f"S3 connection test failed: {e}")
            return False


# Global instance for reuse
s3_uploader = None


def get_s3_uploader() -> S3ImageUploader:
    """Get or create S3 uploader instance."""
    global s3_uploader
    if s3_uploader is None:
        s3_uploader = S3ImageUploader()
    return s3_uploader
