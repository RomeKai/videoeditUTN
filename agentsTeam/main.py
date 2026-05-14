import os
import sys
from crewai import Crew, Task, Process
from agents import EngineeringFactory
from contracts import TechnicalSpecification, QAReport
from dotenv import load_dotenv

load_dotenv()

def run_factory(user_requirement: str):
    """
    Orchestrates the AI Engineering Factory with strict rate limits and targeted tasks.
    Now generalized to accept any user requirement.
    """
    print(f"Initializing Engineering Factory for: {user_requirement}")
    
    # Initialize Agents from the Factory
    architect = EngineeringFactory.architect()
    engineer = EngineeringFactory.engineer()
    qa_auditor = EngineeringFactory.qa_tester()

    # 1. Analysis & Design Task: Targeted file reading to save tokens
    analysis_task = Task(
        description=(
            f"Analyze the following requirement: '{user_requirement}'. "
            "1. Read 'saas_context.md' for infrastructure rules and current architecture. "
            "2. Identify and read relevant project files to understand the current implementation (if applicable). "
            "3. Design a step-by-step technical implementation plan and define necessary data structures."
        ),
        expected_output="A structured JSON TechnicalSpecification object.",
        agent=architect,
        output_pydantic=TechnicalSpecification
    )

    # 2. Implementation Task: Sequential context from Architect
    implementation_task = Task(
        description=(
            "Based on the Architect's TechnicalSpecification: "
            "1. Implement the solution following all architectural guidelines and best practices. "
            "2. Use MoviePy 2.0+ for video processing if required. "
            "3. Ensure the implementation is modular, typed, and follows SOLID principles."
        ),
        expected_output="Functional Python source code for the requested feature.",
        agent=engineer,
        context=[analysis_task]
    )

    # 3. Quality Audit Task: Final validation
    audit_task = Task(
        description=(
            "Audit the implementation for quality, security, and performance: "
            "1. Verify the logic meets all requirements and handles edge cases. "
            "2. Check for potential performance bottlenecks or security risks. "
            "3. Provide the final approved version of the code."
        ),
        expected_output="A structured QAReport JSON.",
        agent=qa_auditor,
        context=[implementation_task],
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
    print("AI Engineering Factory Online (Survival Mode)")
    
    # Accept requirement from command line or use default
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
            
            # Access pydantic data
            # The result object from CrewAI 0.28+ might be a CrewOutput
            # We try to extract the final_source_code from the last task output
            if hasattr(result, 'pydantic'):
                delivery_code = result.pydantic.final_source_code
            elif isinstance(result, dict) and 'final_source_code' in result:
                delivery_code = result['final_source_code']
            else:
                # Fallback: try to find it in the raw string if pydantic failed
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
