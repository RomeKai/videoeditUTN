import os
from dotenv import load_dotenv
from crewai import Agent
from crewai_tools import FileReadTool
from .mcp_engram_client import EngramTool

load_dotenv()

google_key = os.getenv("GOOGLE_API_KEY", "")
os.environ["GEMINI_API_KEY"] = google_key
os.environ["OPENAI_API_KEY"] = google_key # Routing trick for LiteLLM

# Utility to load Gentle-AI Skills
def load_skill(filename):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, 'skills', filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

tool_leer_codigo = FileReadTool()
tool_engram = EngramTool()

class EngineeringFactory:
    @staticmethod
    def architect():
        # PHASE 1: Strong reasoning (Pro) + Memory (Engram) + SDD Skill
        return Agent(
            role="Lead Software Architect",
            goal="Design architectural blueprints and Technical Specifications (SDD) in Markdown.",
            backstory=load_skill("architect_skill.md"),
            tools=[tool_leer_codigo, tool_engram],
            max_iter=5,
            allow_delegation=False,
            verbose=True,
            llm='gemini/gemini-1.5-pro-latest' 
        )

    @staticmethod
    def engineer():
        # PHASE 2: Fast execution (Flash) + Translation focus
        return Agent(
            role="Senior Video Backend Developer",
            goal="Translate the Architect's Markdown SDD into functional Python code.",
            backstory=load_skill("developer_skill.md"),
            tools=[tool_leer_codigo],
            max_iter=3,
            allow_delegation=False,
            verbose=True,
            llm='gemini/gemini-1.5-flash-latest'
        )

    @staticmethod
    def qa_tester():
        # PHASE 3: Fast auditing (Flash) + Memory (Engram) for persistence
        return Agent(
            role="Critique-Bot (QA Reviewer)",
            goal="Ensure the code strictly adheres to the SDD. Guard memory with Engram.",
            backstory=load_skill("reviewer_skill.md"),
            tools=[tool_leer_codigo, tool_engram],
            max_iter=3,
            allow_delegation=True,
            verbose=True,
            llm='gemini/gemini-1.5-flash-latest'
        )
