import os
from crewai import Crew, Task, Process
from agents import EngineeringFactory
from contracts import TechnicalSpecification, QAReport
from dotenv import load_dotenv

load_dotenv()

def run_factory(user_requirement: str):
    """
    Orchestrates the AI Engineering Factory with strict rate limits and targeted tasks.
    """
    print(f"🚀 Initializing Engineering Factory for: {user_requirement}")
    
    # Initialize Agents from the Factory
    architect = EngineeringFactory.architect()
    engineer = EngineeringFactory.engineer()
    qa_auditor = EngineeringFactory.qa_tester()

    # 1. Analysis & Design Task: Targeted file reading to save tokens
    analysis_task = Task(
        description=(
            f"Analyze the following requirement: '{user_requirement}'. "
            "1. Read 'saas_context.md' for infrastructure rules. "
            "2. Read the specific file 'back/apps/payments/models.py' to understand the current Wallet and Transaction logic. "
            "3. Design a step-by-step technical implementation plan for Coin reservation."
        ),
        expected_output="A structured JSON TechnicalSpecification object.",
        agent=architect,
        output_pydantic=TechnicalSpecification
    )

    # 2. Implementation Task: Sequential context from Architect
    implementation_task = Task(
        description=(
            "Based on the Architect's TechnicalSpecification: "
            "1. Read 'back/apps/payments/models.py' again if needed. "
            "2. Write the complete Python code for 'reserve_funds' with atomic rollback. "
            "3. Ensure the implementation is decoupled and follows Django best practices."
        ),
        expected_output="Functional Python source code for the requested feature.",
        agent=engineer,
        context=[analysis_task]
    )

    # 3. Quality Audit Task: Final validation
    audit_task = Task(
        description=(
            "Audit the implementation for security and financial integrity: "
            "1. Verify atomic rollback logic in the provided code. "
            "2. Check for potential race conditions during fund reservation. "
            "3. Provide the final approved version of the code."
        ),
        expected_output="A structured QAReport JSON.",
        agent=qa_auditor,
        context=[implementation_task],
        output_pydantic=QAReport
    )

    # Assemble the Crew with Strict Rate Limiting (max_rpm=3)
    factory_crew = Crew(
        agents=[architect, engineer, qa_auditor],
        tasks=[analysis_task, implementation_task, audit_task],
        process=Process.sequential,
        max_rpm=3, # SHACKLE: Drastically reduce RPM to protect Free Tier
        verbose=True
    )
    
    return factory_crew.kickoff()

if __name__ == "__main__":
    print("✦ AI Engineering Factory Online (Survival Mode) ✦")
    requirement = "Implement the Coin reservation logic (Transaction.reserve_funds) in the wallet system."
    
    try:
        result = run_factory(requirement)
        print("\n\n✅ [FACTORY DELIVERY COMPLETED]")
        print(result)
    except Exception as e:
        print(f"\n❌ Error: {e}")
