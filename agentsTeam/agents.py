from crewai import Agent
from crewai_tools import DirectoryReadTool, FileReadTool
from langchain_google_genai import ChatGoogleGenerativeAI
import os

# Configuration for deterministic Gemini API (Free Tier)
llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",
    verbose=True,
    temperature=0.0,
    google_api_key=os.getenv("GOOGLE_API_KEY")
)

# Tools for codebase interaction
# Dynamic path: points to the 'back' directory at the same level as 'agentsTeam'
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
repo_dir = os.path.join(base_dir, "back")
directory_tool = DirectoryReadTool(directory=repo_dir)
file_tool = FileReadTool()

class EngineeringFactory:
    @staticmethod
    def architect():
        return Agent(
            role="Lead Software Architect",
            goal="Scan codebase and design architectural contracts using GoF/GRASP.",
            backstory="Expert at mapping dependencies and ensuring SOLID compliance. "
                      "You generate structured Architecture Contracts.",
            tools=[directory_tool, file_tool],
            llm=llm,
            allow_delegation=False,
            verbose=True
        )

    @staticmethod
    def engineer():
        return Agent(
            role="Senior Video Backend Developer",
            goal="Implement logic following the Architecture Contract and MoviePy 2.0 standards.",
            backstory="Master of Python, Celery, and FFmpeg. You write clean, performant, and decoupled code.",
            tools=[file_tool],
            llm=llm,
            allow_delegation=False,
            verbose=True
        )

    @staticmethod
    def qa_tester():
        return Agent(
            role="Automated Testing & Audit Expert",
            goal="Verify code efficiency, security, and video processing integrity.",
            backstory="Hard-to-please auditor. You check for CPU spikes, frame skipping, "
                      "and proper OAuth2 token handling.",
            tools=[file_tool],
            llm=llm,
            allow_delegation=True,
            verbose=True
        )
