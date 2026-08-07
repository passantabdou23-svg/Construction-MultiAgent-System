import chromadb

class ConstructionRAG:
    def __init__(self):
        # Initialize persistent or in-memory Chroma database
        self.client = chromadb.Client()
        self.collection = self.client.get_or_create_collection("building_codes")
        self._seed_specifications()

    def _seed_specifications(self):
        # Store essential structural and material standards in vector DB
        docs = [
            "ACI 318 Concrete Standard: Foundation columns subject to high stress must use high-performance concrete C80/90 or C50/60 with a minimum curing period of 14 days.",
            "ASTM A36 Standard Specification: Structural steel rebar and plates require anti-corrosive coating when installed in sub-grade conditions.",
            "BS 8110 Slab Code: Suspended ground slabs require minimum mesh reinforcement B793 with standard 28-day concrete strength test.",
            "Eurocode 2: Structural concrete design requires strict verification of compressive strength classes prior to formwork removal."
        ]
        ids = ["code_1", "code_2", "code_3", "code_4"]
        
        # Avoid duplicate insertions
        if self.collection.count() == 0:
            self.collection.add(
                documents=docs,
                ids=ids
            )

    def query_spec(self, query: str, n_results: int = 1) -> str:
        """Retrieves top matching building standard context for LLM grounding."""
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )
        if results and results['documents'] and len(results['documents'][0]) > 0:
            return results['documents'][0][0]
        return "No specific building code constraint found."

# Quick test execution
if __name__ == "__main__":
    rag = ConstructionRAG()
    context = rag.query_spec("foundation column concrete grade")
    print(f"🔍 RAG Retrieved Context:\n{context}")