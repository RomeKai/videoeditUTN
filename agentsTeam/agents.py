import os
from dotenv import load_dotenv
from crewai import Agent
from crewai_tools import FileReadTool

load_dotenv()

# FIX: Read the API key and force an empty string as default to avoid "str | None" type errors.
google_key = os.getenv("GOOGLE_API_KEY", "")

# GLOBAL FIX: Force CrewAI (Agents, Tools, and Tasks) to use Gemini 1.5 Flash.
# Based on diagnostic, 'gemini-flash-latest' is the valid identifier for this.
os.environ["GEMINI_API_KEY"] = google_key
os.environ["OPENAI_API_KEY"] = google_key # Routing trick for LiteLLM
os.environ["OPENAI_MODEL_NAME"] = "gemini/gemini-flash-latest" 

# Use only FileReadTool to prevent 'Fanning Out' and excessive token consumption.
tool_leer_codigo = FileReadTool()

class EngineeringFactory:
    @staticmethod
    def architect():
        return Agent(
            role="Lead Software Architect",
            goal="Scan specific code files and design architectural contracts using GoF/GRASP.",
            backstory="Expert at mapping dependencies and ensuring SOLID compliance. "
                      "You generate structured Architecture Contracts based on provided files.",
            tools=[tool_leer_codigo],
            max_iter=3, # SHACKLE: Limit iterations to save quota
            allow_delegation=False,
            verbose=True,
            llm='gemini/gemini-flash-latest'
        )

    @staticmethod
    def engineer():
        return Agent(
            role="Senior Video Backend Developer",
            goal="Implement logic following the Architecture Contract and MoviePy 2.0 standards.",
            backstory="Master of Python, Celery, and FFmpeg. You write clean, performant, and decoupled code.",
            tools=[tool_leer_codigo],
            max_iter=3, # SHACKLE: Limit iterations to save quota
            allow_delegation=False,
            verbose=True,
            llm='gemini/gemini-flash-latest'
        )

    @staticmethod
    def qa_tester():
        return Agent(
            role="Automated Testing & Audit Expert",
            goal="Verify code efficiency, security, and video processing integrity.",
            backstory="Hard-to-please auditor. You check for CPU spikes, frame skipping, "
                      "and proper OAuth2 token handling.",
            tools=[tool_leer_codigo],
            max_iter=3, # SHACKLE: Limit iterations to save quota
            allow_delegation=True,
            verbose=True,
            llm='gemini/gemini-flash-latest'
        )
