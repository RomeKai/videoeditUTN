import os
import sys
from crewai import Crew, Task, Process
from agents import EngineeringFactory
from contracts import TechnicalSpecification, QAReport
from dotenv import load_dotenv

load_dotenv()

def run_factory(user_requirement: str):
    """
    Orchestrates the AI Engineering Factory using Gentle-AI SDD Principles.
    """
    print(f"Initializing SDD Engineering Factory for: {user_requirement}")
    
    # Initialize Agents from the Factory
    architect = EngineeringFactory.architect()
    engineer = EngineeringFactory.engineer()
    qa_auditor = EngineeringFactory.qa_tester()

    # PHASE 1: Architect - Spec-Driven Development
    analysis_task = Task(
        description=(
            f"Analyze the following requirement: '{user_requirement}'.\n"
            "1. Read 'saas_context.md' for infrastructure rules.\n"
            "2. Identify and read relevant project files.\n"
            "3. [MEMORY CHECK] Query historical architectural decisions (Simulated Engram memory) before proceeding.\n"
            "4. Output the immutable TechnicalSpecification (SDD) blueprint. Do NOT write functional code."
        ),
        expected_output="A structured JSON TechnicalSpecification object.",
        agent=architect,
        output_pydantic=TechnicalSpecification
    )

    # PHASE 2: Executor - Translation
    implementation_task = Task(
        description=(
            "Based strictly on the Architect's TechnicalSpecification: \n"
            "1. Implement the requested source code.\n"
            "2. Follow the architectural guidelines and design patterns exactly as specified.\n"
            "3. Do not invent new features or libraries outside the Spec."
        ),
        expected_output="Functional Python source code.",
        agent=engineer,
        context=[analysis_task]
    )

    # PHASE 3: Reviewer - QA & Veto
    audit_task = Task(
        description=(
            "Audit the implementation against the SDD: \n"
            "1. Compare the Developer's code against the Architect's TechnicalSpecification.\n"
            "2. Reject the code (is_approved=False) if it deviates from the Spec.\n"
            "3. Verify security and asynchronous boundaries (e.g., Celery, transaction locks).\n"
            "4. Provide the final approved version of the code only if it passes all checks."
        ),
        expected_output="A structured QAReport JSON.",
        agent=qa_auditor,
        context=[analysis_task, implementation_task], # Needs both to compare
        output_pydantic=QAReport
    )

    # Assemble the Crew
    factory_crew = Crew(
        agents=[architect, engineer, qa_auditor],
        tasks=[analysis_task, implementation_task, audit_task],
        process=Process.sequential,
        max_rpm=3, # SHACKLE: Protect quota
        verbose=True
    )
    
    return factory_crew.kickoff()

if __name__ == "__main__":
    print("Gentle-AI Engineering Factory Online (SDD Mode)")
    
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
            
            if hasattr(result, 'pydantic'):
                delivery_code = result.pydantic.final_source_code
            elif isinstance(result, dict) and 'final_source_code' in result:
                delivery_code = result['final_source_code']
            else:
                import re
                code_match = re.search(r"final_source_code='(.*?)'", str(result), re.DOTALL)
                if code_match:
                    delivery_code = code_match.group(1).replace("\\n", "\n").replace("\\'", "'")
                else:
                    delivery_code = str(result)
            
            delivery_path = "agentsTeam/delivery/latest_code.py"
            os.makedirs(os.path.dirname(delivery_path), exist_ok=True)
            with open(delivery_path, "w", encoding="utf-8") as f:
                f.write(delivery_code)
            
            print(f"Code successfully persisted to: {delivery_path}")
            print("--------------------------------------------------")
        except Exception as save_error:
            print(f"Warning: Could not extract or save final_source_code: {save_error}")

        print(result)
    except Exception as e:
        print(f"\nError: {e}")
