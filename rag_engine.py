import re

class ConstructionRAG:
    def __init__(self):
        self.docs = [
            "ACI 318 Concrete Standard: Foundation columns subject to high stress must use high-performance concrete C80/90 or C50/60 with a minimum curing period of 14 days.",
            "ASTM A36 Standard Specification: Structural steel rebar and plates require anti-corrosive coating when installed in sub-grade conditions.",
            "BS 8110 Slab Code: Suspended ground slabs require minimum mesh reinforcement B793 with standard 28-day concrete strength test.",
            "Eurocode 2: Structural concrete design requires strict verification of compressive strength classes prior to formwork removal."
        ]

    def query_spec(self, query: str, n_results: int = 1) -> str:
        """Retrieves top matching building standard context via keyword/semantic match."""
        query_words = set(re.findall(r'\w+', query.lower()))
        
        best_doc = self.docs[0]
        max_score = -1

        for doc in self.docs:
            doc_words = set(re.findall(r'\w+', doc.lower()))
            overlap = len(query_words.intersection(doc_words))
            if overlap > max_score:
                max_score = overlap
                best_doc = doc

        return best_doc

if __name__ == "__main__":
    rag = ConstructionRAG()
    context = rag.query_spec("foundation column concrete grade")
    print(f"🔍 RAG Retrieved Context:\n{context}")