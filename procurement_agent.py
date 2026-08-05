import sqlite3
import json
import ollama

DB_NAME = "construction_mas.db"

class LocalLLMProcurementAgent:
    def execute(self, revision_id: str) -> dict:
        print(f"\n📦 [2. PROCUREMENT AGENT - LOCAL LLM]: Evaluating order for revision {revision_id}...")

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT item_id, material_type, specification, quantity, unit, affected_element 
            FROM design_revisions 
            WHERE revision_id = ?
        """, (revision_id,))
        
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("   -> No materials found for this revision. Skipping procurement.")
            return {
                "revision_id": revision_id,
                "status": "SKIPPED",
                "reason": "No actionable materials identified"
            }

        item_id, material_type, specification, quantity, unit, affected_element = rows[0]

        prompt = f"""
        You are a Construction Procurement Agent. 
        Evaluate supplier lead times and costs for the following item:
        - Revision: {revision_id}
        - Material Type: {material_type}
        - Specification: {specification}
        - Quantity: {quantity} {unit}
        - Element: {affected_element}

        Return ONLY a JSON object:
        {{
            "supplier_name": "string",
            "unit_cost": float,
            "total_cost": float,
            "lead_time_days": int,
            "earliest_delivery_date": "YYYY-MM-DD"
        }}
        """

        try:
            response = ollama.chat(
                model="llama3.1",
                messages=[{"role": "user", "content": prompt}]
            )
            raw_content = response['message']['content'].strip()
            
            if "```json" in raw_content:
                raw_content = raw_content.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_content:
                raw_content = raw_content.split("```")[1].split("```")[0].strip()
                
            parsed_json = json.loads(raw_content)
            
            # If the LLM returns a list, take the first element
            if isinstance(parsed_json, list) and len(parsed_json) > 0:
                procurement_info = parsed_json[0]
            elif isinstance(parsed_json, dict):
                procurement_info = parsed_json
            else:
                raise ValueError("Unexpected JSON format from LLM")

        except Exception as e:
            print(f"   -> LLM parsing note: Using fallback calculations ({e})")
            procurement_info = {
                "supplier_name": "Apex Readymix Co.",
                "unit_cost": 120.0,
                "total_cost": (quantity or 1.0) * 120.0,
                "lead_time_days": 7,
                "earliest_delivery_date": "2026-08-12"
            }

        # Ensure dictionary safety
        if not isinstance(procurement_info, dict):
            procurement_info = {
                "supplier_name": "Apex Readymix Co.",
                "unit_cost": 120.0,
                "total_cost": 500.0,
                "lead_time_days": 5,
                "earliest_delivery_date": "2026-08-12"
            }

        # Save Procurement details into database
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO procurement_records 
            (revision_id, item_id, supplier_name, unit_cost, total_cost, lead_time_days, earliest_delivery_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            revision_id,
            item_id or "MAT-GENERIC",
            procurement_info.get("supplier_name", "Apex Readymix Co."),
            procurement_info.get("unit_cost", 100.0),
            procurement_info.get("total_cost", 500.0),
            procurement_info.get("lead_time_days", 5),
            procurement_info.get("earliest_delivery_date", "2026-08-12")
        ))
        conn.commit()
        conn.close()

        print(f"   -> Supplier: {procurement_info.get('supplier_name')}")
        print(f"   -> Estimated Lead Time: {procurement_info.get('lead_time_days')} days")
        return procurement_info