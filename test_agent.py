import json
import ollama
from database import save_design_revision
from schemas import DesignUpdatePayload  # Your Pydantic schema

class LocalLLMDesignAgent:
    def execute(self, raw_unstructured_text: str) -> dict:
        print("\n🏗️  [1. DESIGN AGENT - LOCAL LLM]: Processing site note via Ollama...")

        system_prompt = (
            "You are a Construction BIM Specialist Agent. Parse raw engineering notes "
            "and output strictly valid JSON matching the requested fields."
        )

        # Call local Ollama instance with Pydantic JSON Schema enforcement
        response = ollama.chat(
            model="llama3.1",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract details from this note into JSON: {raw_unstructured_text}"}
            ],
            format=DesignUpdatePayload.model_json_schema(), # Enforces Pydantic schema
        )

        # Parse JSON output from local model
        parsed_data = json.loads(response["message"]["content"])

        # Persist to local SQLite DB
        save_design_revision(parsed_data)

        req = parsed_data["requirements"][0]
        print(f"   -> Extracted: {req['specification']} ({req['quantity']} {req['unit']})")
        print(f"   -> Saved Revision {parsed_data['revision_id']} to Database")
        
        return parsed_data

# Quick Test
if __name__ == "__main__":
    agent = LocalLLMDesignAgent()
    sample_site_note = "Site inspection on Rev-802: Need 450 meters of 25mm High-Tensile Rebar for footing grid."
    agent.execute(sample_site_note)


    ##test trigger so the script actually runs when executed
    
if __name__ == "__main__":
    agent = LocalLLMDesignAgent()
    sample_site_note = "Site inspection on Rev-802: Need 450 meters of 25mm High-Tensile Rebar for footing grid."
    agent.execute(sample_site_note)