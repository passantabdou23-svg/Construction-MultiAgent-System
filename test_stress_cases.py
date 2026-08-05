import sqlite3
import json
from agent_pipeline import run_construction_agent_pipeline

# Edge-case scenarios to test system resilience
TEST_SCENARIOS = [
    {
        "name": "Scenario 1: Missing Units & Ambiguous Quantity",
        "note": "Site update: Need some extra steel rebar for the top slab ASAP. Maybe 50 or 60 pieces?"
    },
    {
        "name": "Scenario 2: Contradictory Material Specs",
        "note": "Rev-999: Change foundation columns to C50/60 concrete. Wait, strike that, make it C80/90 high performance concrete instead."
    },
    {
        "name": "Scenario 3: Non-Construction / Irrelevant Input",
        "note": "Hey team, don't forget we have a pizza party in the site trailer this Friday at 12 PM!"
    }
]

def run_stress_tests():
    print("=" * 70)
    print("🧪 RUNNING MULTI-AGENT STRESS TESTS & EDGE-CASE EVALUATION")
    print("=" * 70)

    for idx, test in enumerate(TEST_SCENARIOS, 1):
        print(f"\n----------------------------------------------------------------------")
        print(f"▶️ RUNNING TEST {idx}: {test['name']}")
        print(f"   Input Note: \"{test['note']}\"")
        print(f"----------------------------------------------------------------------")
        
        try:
            run_construction_agent_pipeline(test['note'])
            print(f"\n✅ TEST {idx} EXECUTED WITHOUT CRASHING")
        except Exception as e:
            print(f"\n❌ TEST {idx} FAILED WITH ERROR: {e}")

if __name__ == "__main__":
    run_stress_tests()