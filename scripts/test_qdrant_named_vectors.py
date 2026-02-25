"""
Smoke test: Qdrant single vector for page_playbooks.
Verifies that one vector per point (title + situation) works correctly,
including search and ID filtering.

Run: poetry run python scripts/test_qdrant_named_vectors.py
"""

import os
import uuid
from qdrant_client import QdrantClient, models


QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT_REST", "6333"))
COLLECTION = "test_page_playbooks"
VECTOR_DIM = 8  # small dim for testing (real: 3072 for text-embedding-3-large)


def fake_embed(text: str) -> list[float]:
    """Deterministic fake embedding for testing (NOT for production)."""
    import hashlib

    h = hashlib.sha256(text.encode()).digest()
    return [b / 255.0 for b in h[:VECTOR_DIM]]


def main():
    print(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}...")
    client = QdrantClient(
        host=QDRANT_HOST,
        port=QDRANT_PORT,
        check_compatibility=False,
    )

    # Clean up if exists
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)

    # ================================================================
    # TEST 1: Create collection with single vector
    # ================================================================
    print("\n1. Creating collection with single vector...")
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=models.VectorParams(
            size=VECTOR_DIM, distance=models.Distance.COSINE
        ),
    )
    info = client.get_collection(COLLECTION)
    assert info.config.params.vectors is not None
    print("   PASS: Collection created with single vector.")

    # ================================================================
    # TEST 2: Upsert points with 1 vector each (title + situation)
    # ================================================================
    print("\n2. Upserting 3 test playbooks...")
    playbooks = [
        {
            "id": str(uuid.uuid4()),
            "title": "Khach hoi gia",
            "situation": "Khach hang hoi gia san pham",
            "content": "Dung voi bao gia. Hoi lai san pham cu the.",
            "tags": ["pricing"],
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Khach phan nan",
            "situation": "Khach hang phan nan ve chat luong",
            "content": "Xin loi va hoi chi tiet. Vi du: giay bi bong keo sau 2 ngay.",
            "tags": ["complaint"],
        },
        {
            "id": str(uuid.uuid4()),
            "title": "Khach hoi ship",
            "situation": "Khach hang hoi ve van chuyen giao hang",
            "content": "Thong bao phi ship va thoi gian giao hang du kien.",
            "tags": ["shipping"],
        },
    ]

    points = []
    for pb in playbooks:
        points.append(
            models.PointStruct(
                id=pb["id"],
                vector=fake_embed(pb["title"] + "\n" + pb["situation"]),
                payload={
                    "title": pb["title"],
                    "situation": pb["situation"],
                    "content": pb["content"],
                    "tags": pb["tags"],
                },
            )
        )
    client.upsert(collection_name=COLLECTION, points=points)
    print(f"   PASS: Upserted {len(points)} points with 1 vector each.")

    # ================================================================
    # TEST 3: Search by situation (single vector)
    # ================================================================
    print("\n3. Searching by situation...")
    query_vec = fake_embed("khach hoi gia")
    response = client.query_points(
        collection_name=COLLECTION,
        query=query_vec,
        limit=3,
    )
    results = response.points
    assert len(results) > 0, "Expected at least 1 result from search"
    print(f"   PASS: Got {len(results)} results.")
    for r in results:
        score = getattr(r, "score", 0.0)
        print(f"     - {r.payload['title']} (score: {score:.4f})")

    # ================================================================
    # TEST 4: Search with ID filter (assignment simulation)
    # ================================================================
    print("\n4. Searching with ID filter...")
    allowed_ids = [playbooks[0]["id"], playbooks[2]["id"]]
    response = client.query_points(
        collection_name=COLLECTION,
        query=query_vec,
        query_filter=models.Filter(
            must=[models.HasIdCondition(has_id=allowed_ids)]
        ),
        limit=3,
    )
    results = response.points
    for r in results:
        point_id = str(r.id) if hasattr(r.id, "uuid") else r.id
        assert point_id in allowed_ids, f"Got unexpected ID {point_id}"
    print(
        f"   PASS: Got {len(results)} results, all within filtered {len(allowed_ids)} IDs."
    )

    # ================================================================
    # CLEANUP
    # ================================================================
    client.delete_collection(COLLECTION)
    print("\n--- Cleaned up test collection ---")

    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print("  Single vector (title + situation):  PASS")
    print("  Search:                             PASS")
    print("  ID filtering:                        PASS")
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
