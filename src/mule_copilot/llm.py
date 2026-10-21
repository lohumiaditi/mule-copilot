from langchain_groq import ChatGroq

from mule_copilot import config


def chat():
    return ChatGroq(model=config.GROQ_MODEL, temperature=0, max_retries=4)
