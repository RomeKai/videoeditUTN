from crewai import Crew, Task, Process
from agents import EngineeringFactory
from contracts import ArchitectureContract, QAApprovalContract
import os
from dotenv import load_dotenv

load_dotenv()

def run_factory(requirement: str):
    # Instantiate the team
    architect = EngineeringFactory.architect()
    developer = EngineeringFactory.engineer()
    auditor = EngineeringFactory.qa_tester()

    # Task 1: Architectural Design
    task_architecture = Task(
        description=f"Analyze the following requirement: '{requirement}'. Scan the /back directory to understand the impact. Create a professional Architecture Contract.",
        expected_output="A structured ArchitectureContract object with proposed files and design patterns.",
        agent=architect,
        output_json=ArchitectureContract
    )

    # Task 2: Implementation
    task_implementation = Task(
        description="Based on the Architecture Contract, implement the necessary code. Use MoviePy 2.0+ and ensure all logic is decoupled and asynchronous.",
        expected_output="The full source code for the modified or new files.",
        agent=developer,
        context=[task_architecture]
    )

    # Task 3: Quality Audit
    task_audit = Task(
        description="Review the implementation. Check for security (OAuth2), performance (CPU/Memory), and video consistency. Generate a QA Approval report.",
        expected_output="A QAApprovalContract report confirming if the code is ready for production.",
        agent=auditor,
        context=[task_implementation],
        output_json=QAApprovalContract
    )

    # Assemble the Crew
    factory_crew = Crew(
        agents=[architect, developer, auditor],
        tasks=[task_architecture, task_implementation, task_audit],
        process=Process.sequential, # Strict engineering pipeline
        verbose=True
    )

    return factory_crew.kickoff()

if __name__ == "__main__":
    print("🚀 All-in-One Viral Studio Factory is Online.")
    user_req = input("Enter the engineering requirement: ")
    result = run_factory(user_req)
    print("\n--- FINAL OUTPUT ---")
    print(result)
