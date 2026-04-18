# Vector Storage and Semantic Search

To enable features such as searching for specific moments within a video or **Prompt-to-Edit** functionality, we store numerical representations (embeddings) of the content.

## Selected Solution: `pgvector` (PostgreSQL)

The **pgvector** extension, integrated directly into our primary PostgreSQL database, has been selected for vector storage.

### Technical Justification:

1.  **Operational Simplicity:** Utilizing pgvector eliminates the need for additional external services (such as Pinecone or Weaviate). This reduction in infrastructure complexity minimizes latency and operational costs.
2.  **Data Consistency:** Vectors are stored within the same rows as clip metadata, eliminating the risk of synchronization issues between a standalone vector database and the relational database.
3.  **Hybrid Queries:** The system supports queries that combine traditional relational filters (e.g., `user_id = X`) with semantic search (e.g., `video similar to 'motivational speech'`) in a single SQL statement.
4.  **Simplified Backups:** Since all data resides within PostgreSQL, a single backup command secures both user data and AI indices.

---

## Use Cases in OneCreator

### 1. Semantic Clip Search
Users can search using natural language (e.g., "Find the moment where I discuss investment"), and the system will locate the exact segment by comparing the text vector against the transcription vectors.

### 2. Contextual Prompt-to-Edit
When a prompt like "Create a dynamic cut during the funny part" is received, the system utilizes embeddings to identify moments with high "humor" or "excitement" scores previously detected during analysis.
