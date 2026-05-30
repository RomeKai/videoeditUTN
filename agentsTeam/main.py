import os
import sys
from crewai import Crew, Task, Process
from agents import EngineeringFactory
from contracts import TechnicalSpecification, QAReport
from dotenv import load_dotenv

load_dotenv()

def run_factory(user_requirement: str):
    """
    Orchestrates the AI Engineering Factory using Gentle-AI SDD & Engram Memory.
    """
    print(f"Initializing SDD + Engram Factory for: {user_requirement}")
    
    # Initialize Agents from the Factory
    architect = EngineeringFactory.architect()
    engineer = EngineeringFactory.engineer()
    qa_auditor = EngineeringFactory.qa_tester()

    # PHASE 1: Architect - Spec-Driven Development (SDD)
    # Goal: Produce a Markdown Blueprint. NO CODE.
    analysis_task = Task(
        description=(
            f"Analyze the following requirement: '{user_requirement}'.\n"
            "1. Read 'saas_context.md' for infrastructure rules.\n"
            "2. Use 'engram_memory' to search for past architectural decisions related to this task.\n"
            "3. Identify relevant project files and read them.\n"
            "4. DESIGN: Output an immutable Markdown (.md) blueprint. Prohibited from writing functional Python code.\n"
            "5. FINAL: Save the key decisions made here back to 'engram_memory' for future sessions."
        ),
        expected_output="A structured Markdown Technical Specification (SDD) document.",
        agent=architect
    )

    # PHASE 2: Executor - Translation
    # Goal: Read the Blueprint and produce Code.
    implementation_task = Task(
        description=(
            "Consume the Architect's Markdown Technical Specification (SDD).\n"
            "1. Implement the requested source code based strictly on that blueprint.\n"
            "2. Adhere to MoviePy 2.0+ and Django modular monolithic standards.\n"
            "3. Do not invent features or patterns not requested in the SDD."
        ),
        expected_output="Production-ready Python source code.",
        agent=engineer,
        context=[analysis_task]
    )

    # PHASE 3: Reviewer - QA & Veto
    # Goal: Compare Code vs Spec.
    audit_task = Task(
        description=(
            "Verify the implementation's alignment with the initial SDD Blueprint:\n"
            "1. Compare the Senior Developer's code against the Architect's Markdown document.\n"
            "2. Reject (is_approved=False) if the code deviates from the specified patterns or files.\n"
            "3. CHECK: Verify 'select_for_update' for financial logic and R2 storage usage.\n"
            "4. Provide the final approved source code if it passes all quality checks."
        ),
        expected_output="A structured QAReport JSON with the approved code.",
        agent=qa_auditor,
        context=[analysis_task, implementation_task],
        output_pydantic=QAReport
    )

    # Assemble the Crew
    factory_crew = Crew(
        agents=[architect, engineer, qa_auditor],
        tasks=[analysis_task, implementation_task, audit_task],
        process=Process.sequential,
        max_rpm=3, # Quota protection
        verbose=True
    )
    
    return factory_crew.kickoff()

if __name__ == "__main__":
    print("Gentle-AI Factory Online (Memory-Enabled SDD)")
    
    if len(sys.argv) > 1:
        requirement = sys.argv[1]
    else:
        requirement = "Explain the current project structure and suggest an improvement."
    
    try:
        result = run_factory(requirement)
        print("\n\n[FACTORY DELIVERY COMPLETED]")
        
        # PERSISTENCE LAYER
        try:
            os.makedirs("delivery", exist_ok=True)
            
            # The result from Kickoff is the output of the LAST task (audit_task)
            # which is a QAReport pydantic object.
            if hasattr(result, 'pydantic'):
                delivery_code = result.pydantic.final_source_code
            elif isinstance(result, dict) and 'final_source_code' in result:
                delivery_code = result['final_source_code']
            else:
                delivery_code = str(result)
            
            delivery_path = "agentsTeam/delivery/latest_code.py"
            os.makedirs(os.path.dirname(delivery_path), exist_ok=True)
            with open(delivery_path, "w", encoding="utf-8") as f:
                f.write(delivery_code)
            
            print(f"Code successfully persisted to: {delivery_path}")
            print("--------------------------------------------------")
        except Exception as save_error:
            print(f"Warning: Could not extract final_source_code: {save_error}")

        print(result)
    except Exception as e:
        print(f"\nError: {e}")
