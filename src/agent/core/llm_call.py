from typing import Optional, Dict, Any, AsyncGenerator, List, Union, Type
from pydantic import BaseModel
import json
from openai import AsyncOpenAI
from openai.types.responses import ParsedResponse, ResponseStreamEvent
from httpx import Timeout
from src.utils.logger import get_logger

logger = get_logger()

# Timeout configuration for OpenAI API calls
# - connect: Time to establish connection (10s)
# - read: Time to receive streaming response (3 min for long reasoning)
# - write: Time to send request (30s)
# - pool: Time to wait for connection from pool (10s)
DEFAULT_TIMEOUT = Timeout(
    connect=10.0,
    read=180.0,
    write=30.0,
    pool=10.0,
)


class LLM_call:
    def __init__(self, api_key: str, timeout: Timeout = DEFAULT_TIMEOUT):
        if not api_key:
            raise ValueError("API key is required and must be valid")

        self.client = AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def create(
        self,
        model: str = "gpt-5-mini",
        reasoning: Optional[
            Dict[str, Any]
        ] = None,  # {"effort": "medium", "summary": "auto"},
        text: Optional[Dict[str, Any]] = None,  # {"verbosity": "medium"},
        input: List[Dict[str, str]] = None,
        tools: List[Dict[str, Any]] = None,
        tool_choice: Optional[str | Dict[str, Any]] = None,
        parallel_tool_calls: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            params = {"model": model, **kwargs}

            if input is not None:
                params["input"] = input
            if tools is not None:
                params["tools"] = tools
            if tool_choice is not None:
                params["tool_choice"] = tool_choice
            if parallel_tool_calls is not None:
                params["parallel_tool_calls"] = parallel_tool_calls
            if reasoning is not None:
                params["reasoning"] = reasoning
            if text is not None:
                params["text"] = text

            response = await self.client.responses.create(**params)
            result = response.model_dump(mode="json")

            return result

        except Exception as e:
            logger.error(f"Error creating response: {str(e)}")
            raise

    async def stream(
        self,
        model: str = "gpt-5-mini",
        input: List[Dict[str, str]] = None,
        tools: List[Dict[str, Any]] = None,
        tool_choice: Optional[str | Dict[str, Any]] = None,
        parallel_tool_calls: bool = True,
        text_format=None,
        reasoning: Optional[
            Dict[str, Any]
        ] = None,  # {"effort": "medium", "summary": "auto"},
        text: Optional[Dict[str, Any]] = None,  # {"verbosity": "medium"},
        store: bool = True,
        **kwargs,
    ) -> AsyncGenerator[ResponseStreamEvent | ParsedResponse, None]:
        try:
            params = {"model": model, "store": store, **kwargs}

            if input is not None:
                params["input"] = input
            if tools is not None:
                params["tools"] = tools
            if tool_choice is not None:
                params["tool_choice"] = tool_choice
            if parallel_tool_calls is not None:
                params["parallel_tool_calls"] = parallel_tool_calls
            if reasoning is not None:
                params["reasoning"] = reasoning
            if text is not None:
                params["text"] = text
            if text_format is not None:
                params["text_format"] = text_format

            # Create async stream
            async with self.client.responses.stream(**params) as stream:
                async for event in stream:
                    yield event

                final_response = await stream.get_final_response()

                yield final_response

        except Exception as e:
            logger.error(f"Error in stream: {str(e)}")
            raise

    async def parse(
        self,
        model: str = "gpt-5-mini",
        input: List[Dict[str, str]] = None,
        text_format: Union[Type[BaseModel], Dict[str, Any], None] = None,
        reasoning: Optional[Dict[str, Any]] = None,
        return_full_response: bool = False,
        **kwargs,
    ) -> Optional[Any]:
        """
        Parse LLM response with structured output.

        Args:
            model: Model name
            input: List of messages
            text_format: Either a Pydantic model class OR a dict with JSON schema
            reasoning: Reasoning parameters
            return_full_response: If True, returns (parsed_data, full_response_dict). If False, returns only parsed_data
            **kwargs: Additional parameters

        Returns:
            Parsed response object or dict, or tuple (parsed_data, full_response_dict) if return_full_response=True
        """
        try:
            # Detect if text_format is Pydantic or JSON schema dict
            is_pydantic = (
                text_format is not None
                and isinstance(text_format, type)
                and issubclass(text_format, BaseModel)
            )
            is_json_schema = (
                text_format is not None
                and isinstance(text_format, dict)
                and text_format.get("type") == "json_schema"
            )

            if is_pydantic:
                # Use responses.parse() with Pydantic model
                return await self._parse_with_pydantic(
                    model=model,
                    input=input,
                    text_format=text_format,
                    reasoning=reasoning,
                    return_full_response=return_full_response,
                    **kwargs,
                )
            elif is_json_schema:
                # Use responses.create() with JSON schema
                return await self._parse_with_json_schema(
                    model=model,
                    input=input,
                    json_schema=text_format,
                    reasoning=reasoning,
                    return_full_response=return_full_response,
                    **kwargs,
                )
            else:
                # No structured output, use regular create
                return await self._parse_without_format(
                    model=model,
                    input=input,
                    reasoning=reasoning,
                    return_full_response=return_full_response,
                    **kwargs,
                )

        except Exception as e:
            logger.error(f"Error parsing response: {str(e)}")
            raise

    async def _parse_with_pydantic(
        self,
        model: str,
        input: List[Dict[str, str]],
        text_format: Type[BaseModel],
        reasoning: Optional[Dict[str, Any]],
        return_full_response: bool = False,
        **kwargs,
    ):
        """Parse using Pydantic model (recommended)"""
        params = {"model": model, **kwargs}

        if input is not None:
            params["input"] = input
        if text_format is not None:
            params["text_format"] = text_format
        if reasoning is not None:
            params["reasoning"] = reasoning

        response = await self.client.responses.parse(**params)

        if return_full_response:
            # Return tuple: (parsed_data, full_response_dict)
            return response.output_parsed, response.model_dump(mode="json")
        else:
            # Return only the parsed Pydantic object
            return response.output_parsed

    async def _parse_with_json_schema(
        self,
        model: str,
        input: List[Dict[str, str]],
        json_schema: Dict[str, Any],
        reasoning: Optional[Dict[str, Any]],
        return_full_response: bool = False,
        **kwargs,
    ):
        """Parse using raw JSON schema"""
        params = {"model": model, **kwargs}

        if input is not None:
            params["input"] = input

        params["text"] = {
            "format": json_schema,
        }

        if reasoning is not None:
            params["reasoning"] = reasoning

        response = await self.client.responses.create(**params)

        # Find the output item that contains text content (skip reasoning items)
        text_content = None
        for output_item in response.output:
            if (
                hasattr(output_item, "content")
                and output_item.content
                and len(output_item.content) > 0
                and hasattr(output_item.content[0], "text")
                and output_item.content[0].text
            ):
                text_content = output_item.content[0].text
                break

        if not text_content:
            raise ValueError(
                "No text content found in response output. "
                f"Output items: {[type(item).__name__ for item in response.output]}"
            )

        # Parse JSON
        parsed_data = json.loads(text_content)

        if return_full_response:
            # Return tuple: (parsed_data, full_response_dict)
            return parsed_data, response.model_dump(mode="json")
        else:
            # Return only the parsed dict
            return parsed_data

    async def _parse_without_format(
        self,
        model: str,
        input: List[Dict[str, str]],
        reasoning: Optional[Dict[str, Any]],
        return_full_response: bool = False,
        **kwargs,
    ):
        """Parse without structured output"""
        params = {"model": model, **kwargs}

        if input is not None:
            params["input"] = input
        if reasoning is not None:
            params["reasoning"] = reasoning

        response = await self.client.responses.create(**params)

        if return_full_response:
            # Return tuple: (response_obj, response_dict)
            return response, response.model_dump(mode="json")
        else:
            return response
