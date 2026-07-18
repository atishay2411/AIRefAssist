import os
from dotenv import load_dotenv
import streamlit as st

from langchain_community.tools import ArxivQueryRun, WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper, ArxivAPIWrapper
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_groq import ChatGroq

from langchain_core.messages import HumanMessage, AnyMessage
from typing_extensions import TypedDict
from typing import Annotated
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

# === Load environment variables from .env ===
load_dotenv()
print("TAVILY_API_KEY:", os.getenv("TAVILY_API_KEY"))
print("GROQ_API_KEY:", os.getenv("GROQ_API_KEY"))

# === Initialize tools ===
arxiv = ArxivQueryRun(api_wrapper=ArxivAPIWrapper(top_k_results=2, doc_content_chars_max=500))
wiki = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper(top_k_results=1, doc_content_chars_max=500))
tavily = TavilySearchResults()
tools = [arxiv, wiki, tavily]

# === Initialize LLM ===
llm = ChatGroq(model="qwen-qwq-32b")
llm_with_tools = llm.bind_tools(tools)

# === Define LangGraph State ===
class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

def tool_calling_llm(state: State):
    print("Invoking LLM with tools...")
    return {"messages": [llm_with_tools.invoke(state["messages"])]}

# === Build LangGraph ===
builder = StateGraph(State)
builder.add_node("tool_calling_llm", tool_calling_llm)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "tool_calling_llm")
builder.add_conditional_edges("tool_calling_llm", tools_condition)
builder.add_edge("tools", END)
graph = builder.compile()
print("LangGraph compiled successfully.")

# === Streamlit UI ===
st.set_page_config(page_title="LangGraph Chatbot", page_icon="🤖")
st.title("📚 LangGraph Tool-Using Chatbot")

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

user_input = st.chat_input("Ask me anything...")

if user_input:
    print("User input:", user_input)
    st.session_state.chat_history.append(HumanMessage(content=user_input))

    try:
        response = graph.invoke({"messages": st.session_state.chat_history})
        print("LangGraph responded.")
        print("Full response:", response)

        st.session_state.chat_history = response["messages"]

    except Exception as e:
        print("LangGraph invocation failed:", e)
        st.error(f"LangGraph failed: {e}")
        # Fallback to basic LLM
        try:
            fallback_response = llm.invoke(user_input)
            print("Fallback LLM response:", fallback_response)
            st.session_state.chat_history.append(fallback_response)
        except Exception as fallback_error:
            print("Fallback LLM also failed:", fallback_error)
            st.error("LLM failed to respond.")

# === Display full chat history ===
for msg in st.session_state.chat_history:
    print("Message in history:", msg)

    if msg.type == "human":
        st.chat_message("user").markdown(msg.content)
    elif msg.type == "ai":
        content = getattr(msg, "content", None)
        if content:
            source_info = ""
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                source_info = f" _(source: {msg.tool_calls[0].name})_"
                print("Source tool:", msg.tool_calls[0].name)

            print("Assistant response:", content)
            st.chat_message("assistant").markdown(content + source_info)
        else:
            print("Assistant message has no content.")
            st.chat_message("assistant").markdown("_No response content found._")
