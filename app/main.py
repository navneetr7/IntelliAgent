import streamlit as st
import os
from dotenv import load_dotenv
import supabase
import asyncio
import aiohttp
from datetime import datetime, timedelta
from aiohttp import FormData
import logging
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
supabase_client = supabase.create_client(supabase_url, supabase_key)

st.set_page_config(page_title="CogniCrew", page_icon="🤖", layout="wide")
st.markdown("""
    <style>
    .stApp { background-color: #1E1E1E; color: #FFFFFF; }
    .stTextInput > div > div > input { background-color: #2E2E2E; color: #FFFFFF; }
    .stButton > button { background-color: #4CAF50; color: #FFFFFF; }
    .stSidebar { background-color: #2E2E2E; }
    .chat-container { display: flex; flex-direction: column; height: 80vh; }
    .chat-messages { flex-grow: 1; overflow-y: auto; padding: 10px; }
    .chat-input { position: sticky; bottom: 0; padding: 10px; background-color: #1E1E1E; }
    </style>
""", unsafe_allow_html=True)

# Only Single (RAG-Only) mode
mode_map = {"Single (RAG-Only)": "single_rag"}

# Language options for dropdown
LANGUAGE_OPTIONS = [
    "English", "Spanish", "French", "German", "Italian",
    "Portuguese", "Russian", "Chinese", "Japanese", "Korean"
]

# Initialize session state
if "user" not in st.session_state:
    st.session_state.user = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "agents" not in st.session_state:
    st.session_state.agents = []
if "mode" not in st.session_state:
    st.session_state.mode = "Single (RAG-Only)"
if "auth_token" not in st.session_state:
    st.session_state.auth_token = None
if "customer_setup" not in st.session_state:
    st.session_state.customer_setup = False
if "customer_email" not in st.session_state:
    st.session_state.customer_email = None
if "customer_name" not in st.session_state:
    st.session_state.customer_name = None
if "last_activity" not in st.session_state:
    st.session_state.last_activity = datetime.now()
if "current_language" not in st.session_state:
    st.session_state.current_language = "English"  # Track current language
if "selected_department" not in st.session_state:
    st.session_state.selected_department = None  # Track selected department

def restore_session():
    try:
        if st.session_state.auth_token:
            supabase_client.auth.refresh_session(st.session_state.auth_token)
            session = supabase_client.auth.get_session()
            if session and session.user:
                st.session_state.user = session.user
                st.session_state.agents = load_agents(st.session_state.user.id)
                logger.info("Session restored successfully")
                return True
        session = supabase_client.auth.get_session()
        if session and session.user:
            st.session_state.user = session.user
            st.session_state.auth_token = session.access_token
            st.session_state.agents = load_agents(st.session_state.user.id)
            logger.info("Session retrieved successfully")
            return True
        logger.info("No valid session found")
        return False
    except Exception as e:
        logger.error(f"Session restore failed: {e}")
        return False

async def end_session_and_create_ticket():
    """Create a ticket with the full conversation at session end."""
    if not st.session_state.messages or not st.session_state.agents or not st.session_state.selected_department:
        return
    
    # Find agent by selected department
    agent = next((a for a in st.session_state.agents if a["department"] == st.session_state.selected_department), None)
    if not agent:
        logger.error(f"No agent found for department: {st.session_state.selected_department}")
        return
    if not agent.get("create_tickets", False) or not agent.get("helpdesk_platform"):
        return  # Skip if agent doesn't support ticket creation

    formatted_conversation = []
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            formatted_conversation.append(f"{st.session_state.customer_name}: {msg['content']}")
        elif msg["role"] == "assistant":
            content = msg["content"]
            if content.startswith(f"{agent['name']}:"):
                content = content.replace(f"{agent['name']}:", "", 1).strip()
            formatted_conversation.append(f"{agent['name']}: {content}")
    
    full_conversation = "\n".join(formatted_conversation)
    first_message = next((msg["content"] for msg in st.session_state.messages if msg["role"] == "user"), "Chat Session")
    
    async with aiohttp.ClientSession() as session:
        ticket_request = {
            "user_id": st.session_state.user.id,
            "agent_id": agent["id"],
            "message": first_message,
            "response": full_conversation,
            "platform": agent["helpdesk_platform"],
            "department_id": agent.get("helpdesk_department_id", ""),
            "customer_email": st.session_state.customer_email,
            "customer_name": st.session_state.customer_name,
            "agent_name": agent["name"]
        }
        resp = await session.post("http://localhost:8000/create_ticket", json=ticket_request)
        if resp.status == 200:
            ticket_data = await resp.json()
            logger.info(f"Session-end ticket created: {ticket_data}")
        else:
            error_text = await resp.text()
            logger.error(f"Session-end ticket creation failed: {error_text}")

if "user" not in st.session_state or not st.session_state.user:
    if not restore_session():
        st.session_state.user = None
    else:
        if st.session_state.user:
            st.session_state.messages = []
        st.rerun()

def sign_up(email, password):
    try:
        response = supabase_client.auth.sign_up({"email": email, "password": password})
        if response.user:
            st.success("Sign-up successful! Please sign in.")
        else:
            st.error("Sign-up failed.")
    except Exception as e:
        st.error(f"Sign-up failed: {e}")

def sign_in(email, password):
    try:
        response = supabase_client.auth.sign_in_with_password({"email": email, "password": password})
        if response.user:
            st.session_state.user = response.user
            st.session_state.auth_token = response.session.access_token
            st.session_state.agents = load_agents(st.session_state.user.id)
            st.session_state.messages = []
            logger.info(f"Signed in user: {response.user.email}, token: {response.session.access_token[:10]}...")
            st.rerun()
    except Exception as e:
        st.error(f"Sign-in failed: {e}")

def sign_out():
    asyncio.run(end_session_and_create_ticket())
    supabase_client.auth.sign_out()
    st.session_state.user = None
    st.session_state.auth_token = None
    st.session_state.messages = []
    st.session_state.customer_setup = False
    st.session_state.customer_email = None
    st.session_state.customer_name = None
    st.session_state.current_language = "English"
    st.session_state.selected_department = None
    logger.info("User signed out")
    st.rerun()

def load_agents(user_id):
    async def fetch_agents():
        async with aiohttp.ClientSession() as session:
            resp = await session.get(f"http://localhost:8000/list_agents?user_id={user_id}")
            if resp.status != 200:
                logger.error(f"Failed to load agents: {await resp.text()}")
                return []
            data = await resp.json()
            return data["agents"]
    return asyncio.run(fetch_agents())

with st.sidebar:
    st.title("🤖 CogniCrew")
    if not st.session_state.user:
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Sign In"):
                sign_in(email, password)
        with col2:
            if st.button("Sign Up"):
                sign_up(email, password)
    else:
        st.write(f"Welcome, {st.session_state.user.email}")
        if st.button("Sign Out"):
            sign_out()
        
        st.subheader("Settings")
        st.write("Mode: Single (RAG-Only)")

if st.session_state.user:
    st.title("CogniCrew - Your Smart Team")
    tab1, tab2 = st.tabs(["Chat", "Agents"])
    
    with tab1:
        if not st.session_state.customer_setup:
            st.subheader("Let’s Get Started")
            customer_name = st.text_input("Your Name", key="customer_name_input")
            customer_email = st.text_input("Your Email", key="customer_email_input")
            customer_language = st.selectbox("Preferred Language", LANGUAGE_OPTIONS, index=0, key="customer_language_input")
            departments = sorted(set(a.get("department", "General") for a in st.session_state.agents if a.get("department")))
            if not departments:
                departments = ["General"]
            customer_department = st.selectbox("Department", departments, index=0, key="customer_department_input")
            if st.button("Start Chat"):
                if customer_name and customer_email and customer_language and customer_department:
                    async def setup_contact():
                        async with aiohttp.ClientSession() as session:
                            # Use the selected department's agent for setup_contact
                            agent = next((a for a in st.session_state.agents if a["department"] == customer_department), None)
                            agent_id = agent["id"] if agent else None
                            resp = await session.post("http://localhost:8000/setup_contact", json={
                                "user_id": st.session_state.user.id,
                                "agent_id": agent_id,
                                "name": customer_name,
                                "email": customer_email
                            })
                            if resp.status == 200:
                                data = await resp.json()
                                logger.info(f"Contact setup response: {data}")
                                return True
                            else:
                                error_text = await resp.text()
                                logger.error(f"Contact setup failed: {error_text}")
                                return False
                    with st.spinner("Setting up..."):
                        success = asyncio.run(setup_contact())
                        if success:
                            st.session_state.customer_setup = True
                            st.session_state.customer_name = customer_name
                            st.session_state.customer_email = customer_email
                            st.session_state.current_language = customer_language
                            st.session_state.selected_department = customer_department
                            logger.info(f"Customer setup completed: {customer_name} <{customer_email}> in {customer_language} for {customer_department}")
                            st.rerun()
                        else:
                            st.error("Failed to set up contact. Please try again.")
                else:
                    st.error("Please provide name, email, language, and department to start chatting.")
        else:
            st.subheader(f"Chatting as {st.session_state.customer_name} <{st.session_state.customer_email}>")
            departments = sorted(set(a.get("department", "General") for a in st.session_state.agents if a.get("department")))
            if not departments:
                departments = ["General"]
            selected_department = st.selectbox("Select Department", departments, index=departments.index(st.session_state.selected_department) if st.session_state.selected_department in departments else 0)
            if selected_department != st.session_state.selected_department:
                st.session_state.selected_department = selected_department
                st.session_state.messages = []  # Clear messages when switching departments
            
            chat_messages = st.container(height=500)
            with chat_messages:
                for msg in st.session_state.messages:
                    avatar_url = msg.get("avatar_url")
                    with st.chat_message(msg["role"], avatar=avatar_url if avatar_url else None):
                        st.write(msg["content"])
            
            if (datetime.now() - st.session_state.last_activity) > timedelta(minutes=5):
                asyncio.run(end_session_and_create_ticket())
                st.session_state.messages = []
                st.session_state.last_activity = datetime.now()
                st.rerun()

            col1, col2 = st.columns([9, 1])
            with col1:
                user_input = st.chat_input("Ask your crew...")
            with col2:
                if st.button("Clear Chat"):
                    asyncio.run(end_session_and_create_ticket())
                    st.session_state.messages = []
                    st.session_state.last_activity = datetime.now()
                    st.rerun()

            if user_input:
                st.session_state.messages.append({"role": "user", "content": user_input})
                st.session_state.last_activity = datetime.now()
                with chat_messages:
                    with st.chat_message("user"):
                        st.write(user_input)
                
                if not st.session_state.agents:
                    default_agent = {
                        "name": "Dmitri",
                        "role": "Support",
                        "llm_type": "deepseek",
                        "api_key": deepseek_api_key or "",
                        "rag_only": True,
                        "info": "Default support agent",
                        "company": "PhoneMonitor",
                        "helpdesk_platform": "",
                        "helpdesk_client_id": "",
                        "helpdesk_client_secret": "",
                        "helpdesk_refresh_token": "",
                        "helpdesk_org_id": "",
                        "helpdesk_department_id": "",
                        "helpdesk_subdomain": "",
                        "create_tickets": False,
                        "department": "General"
                    }
                    if not default_agent["api_key"]:
                        st.error("No DeepSeek API key found in .env. Please add an agent with a valid API key.")
                    else:
                        async def add_default():
                            async with aiohttp.ClientSession() as session:
                                resp = await session.post("http://localhost:8000/add_agent", json={
                                    "user_id": st.session_state.user.id,
                                    "agent": default_agent
                                })
                                if resp.status != 200:
                                    logger.error(f"Failed to add default agent: {await resp.text()}")
                                    return None
                                return await resp.json()
                        result = asyncio.run(add_default())
                        if result:
                            st.session_state.agents = [{**default_agent, "id": result["agent"]["id"]}]
                
                async def chat_async():
                    async with aiohttp.ClientSession() as session:
                        request_body = {
                            "user_id": st.session_state.user.id,
                            "message": user_input,
                            "mode": mode_map[st.session_state.mode],
                            "agents": st.session_state.agents,
                            "customer_email": st.session_state.customer_email,
                            "customer_name": st.session_state.customer_name,
                            "history": st.session_state.messages,
                            "language": st.session_state.current_language,
                            "department": st.session_state.selected_department  # Pass selected department
                        }
                        logger.info(f"Chat request body: {request_body}")
                        resp = await session.post("http://localhost:8000/chat", json=request_body)
                        if resp.status != 200:
                            error_text = await resp.text()
                            logger.error(f"Chat failed with status {resp.status}: {error_text}")
                            return {"responses": [], "rag_context": ""}
                        response = await resp.json()
                        logger.info(f"Chat response received: {response}")
                        return response
                
                with st.spinner("Thinking..."):
                    response = asyncio.run(chat_async())
                    with chat_messages:
                        for r in response["responses"]:
                            content = r['response']
                            avatar_url = r.get("avatar_url")
                            if "Switched to" in content:
                                new_language = content.split("Switched to")[1].split("!")[0].strip()
                                if new_language in LANGUAGE_OPTIONS:
                                    st.session_state.current_language = new_language
                                    logger.info(f"Language switched to: {new_language}")
                            logger.info(f"Rendering message for {r['agent']} with avatar_url: {avatar_url}")
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": content,
                                "avatar_url": avatar_url
                            })
                            with st.chat_message("assistant", avatar=avatar_url if avatar_url else None):
                                st.write(content)

    with tab2:
        st.subheader("Add New Agent")
        agent_name = st.text_input("Agent Name")
        agent_role = st.text_input("Role")
        agent_persona = st.text_area("Agent Persona (e.g., 'Friendly woman in her 30s')", "A helpful assistant")
        agent_company = st.text_input("Company", "PhoneMonitor")
        agent_department = st.text_input("Department (e.g., Tech Support)", "")
        agent_llm = st.selectbox("LLM", ["deepseek", "gpt", "grok", "gemini"])
        agent_key = st.text_input("API Key", type="password")
        agent_rag_only = st.checkbox("RAG-Only", value=True, disabled=True)
        agent_avatar = st.file_uploader("Agent Avatar (optional)", type=["png", "jpg", "jpeg"], key="new_avatar")
        agent_file = st.file_uploader("Upload RAG File", type=["txt", "pdf"])
        
        st.subheader("Helpdesk Integration (Optional)")
        helpdesk_platform = st.selectbox("Helpdesk Platform", ["None", "Zendesk", "Zoho Desk"])
        helpdesk_subdomain = ""
        if helpdesk_platform != "None":
            if helpdesk_platform == "Zoho Desk":
                helpdesk_client_id = st.text_input("Zoho Client ID")
                helpdesk_client_secret = st.text_input("Zoho Client Secret", type="password")
                helpdesk_refresh_token = st.text_input("Zoho Refresh Token", type="password")
                helpdesk_org_id = st.text_input("Zoho Desk Org ID")
                helpdesk_department_id = st.text_input("Department ID (optional)")
            else:
                helpdesk_client_id = st.text_input("Zendesk API Key", type="password")
                helpdesk_client_secret = ""
                helpdesk_refresh_token = ""
                helpdesk_org_id = ""
                helpdesk_department_id = st.text_input("Department ID (optional)")
                helpdesk_subdomain = st.text_input("Zendesk Subdomain")
            create_tickets = st.checkbox("Create Tickets After Chat", value=False)
        else:
            helpdesk_client_id = helpdesk_client_secret = helpdesk_refresh_token = helpdesk_org_id = helpdesk_department_id = ""
            create_tickets = False
        
        if st.button("Add Agent"):
            if not agent_key:
                st.error("LLM API Key is required for the agent.")
            elif len(st.session_state.agents) < 5:
                if helpdesk_platform == "Zoho Desk" and not all([helpdesk_client_id, helpdesk_client_secret, helpdesk_refresh_token, helpdesk_org_id]):
                    st.error("Zoho Desk requires Client ID, Client Secret, Refresh Token, and Org ID.")
                elif helpdesk_platform == "Zendesk" and not all([helpdesk_client_id, helpdesk_subdomain]):
                    st.error("Zendesk requires API Key and Subdomain.")
                else:
                    new_agent = {
                        "name": agent_name,
                        "role": agent_role,
                        "llm_type": agent_llm,
                        "api_key": agent_key,
                        "rag_only": True,
                        "info": agent_persona,
                        "company": agent_company,
                        "is_default": False,  # No default agent
                        "department": agent_department,
                        "helpdesk_platform": helpdesk_platform.lower() if helpdesk_platform != "None" else "",
                        "helpdesk_client_id": helpdesk_client_id,
                        "helpdesk_client_secret": helpdesk_client_secret,
                        "helpdesk_refresh_token": helpdesk_refresh_token,
                        "helpdesk_org_id": helpdesk_org_id,
                        "helpdesk_department_id": helpdesk_department_id,
                        "helpdesk_subdomain": helpdesk_subdomain,
                        "create_tickets": create_tickets
                    }
                    async def add_async():
                        async with aiohttp.ClientSession() as session:
                            resp = await session.post("http://localhost:8000/add_agent", json={
                                "user_id": st.session_state.user.id, "agent": new_agent
                            })
                            if resp.status != 200:
                                logger.error(f"Failed to add agent: {await resp.text()}")
                                return None
                            return await resp.json()
                    result = asyncio.run(add_async())
                    if result:
                        new_agent["id"] = result["agent"]["id"]
                        st.session_state.agents.append(result["agent"])
                        st.success(result.get("message", f"Added {agent_name} successfully"))
                        if agent_file:
                            async def upload_rag_async():
                                form_data = FormData()
                                form_data.add_field("user_id", st.session_state.user.id)
                                form_data.add_field("agent_id", new_agent["id"])
                                form_data.add_field("file", agent_file, filename=agent_file.name)
                                async with aiohttp.ClientSession() as session:
                                    resp = await session.post("http://localhost:8000/upload_rag", data=form_data)
                                    return await resp.json()
                            result = asyncio.run(upload_rag_async())
                            st.success(result.get("message", "RAG file uploaded successfully"))
                        if agent_avatar:
                            async def upload_avatar_async():
                                form_data = FormData()
                                form_data.add_field("user_id", st.session_state.user.id)
                                form_data.add_field("agent_id", new_agent["id"])
                                form_data.add_field("file", agent_avatar, filename=agent_avatar.name)
                                async with aiohttp.ClientSession() as session:
                                    resp = await session.post("http://localhost:8000/upload_avatar", data=form_data)
                                    return await resp.json()
                            result = asyncio.run(upload_avatar_async())
                            if "avatar_url" in result:
                                st.session_state.agents[-1]["avatar_url"] = result["avatar_url"]
                                st.success(result.get("message", "Avatar uploaded successfully"))
                            else:
                                st.error("Avatar upload failed. Check logs for details.")
                        st.rerun()
            else:
                st.warning("Limit reached (5 agents).")
        
        st.subheader("Manage Existing Agents")
        for i, agent in enumerate(st.session_state.agents):
            with st.expander(f"{agent['name']})"):
                if agent.get("avatar_url"):
                    st.image(agent["avatar_url"], width=100, caption=f"{agent['name']}'s Avatar")
                st.write(f"Role: {agent['role']}")
                st.write(f"Persona: {agent.get('info', 'N/A')}")
                st.write(f"Company: {agent.get('company', 'N/A')}")
                st.write(f"Department: {agent.get('department', 'N/A')}")
                st.write(f"LLM: {agent['llm_type']} (RAG-Only)")
                st.write(f"Helpdesk: {agent['helpdesk_platform'] or 'None'} {'(Tickets Enabled)' if agent['create_tickets'] else ''}")
                if agent.get("helpdesk_department_id"):
                    st.write(f"Department ID: {agent['helpdesk_department_id']}")
                
                edit_name = st.text_input(f"Edit Name", agent["name"], key=f"edit_name_{i}")
                edit_role = st.text_input(f"Edit Role", agent["role"], key=f"edit_role_{i}")
                edit_persona = st.text_area(f"Edit Agent Persona (e.g., 'Friendly woman in her 30s')", agent.get("info", ""), key=f"edit_info_{i}")
                edit_company = st.text_input(f"Edit Company", agent.get("company", ""), key=f"edit_company_{i}")
                edit_department = st.text_input(f"Edit Department", agent.get("department", ""), key=f"edit_dept_{i}")
                edit_llm = st.selectbox(f"Edit LLM", ["deepseek", "gpt", "grok", "gemini"], index=["deepseek", "gpt", "grok", "gemini"].index(agent["llm_type"]), key=f"edit_llm_{i}")
                edit_key = st.text_input(f"Edit API Key", agent["api_key"], type="password", key=f"edit_key_{i}")
                edit_rag_only = st.checkbox(f"Edit RAG-Only", value=True, disabled=True, key=f"edit_rag_{i}")
                
                platform_map = {"none": "None", "zendesk": "Zendesk", "zoho desk": "Zoho Desk"}
                current_platform = platform_map.get(agent["helpdesk_platform"].lower(), "None")
                edit_helpdesk_platform = st.selectbox(
                    f"Edit Helpdesk Platform",
                    ["None", "Zendesk", "Zoho Desk"],
                    index=["None", "Zendesk", "Zoho Desk"].index(current_platform),
                    key=f"edit_helpdesk_{i}"
                )
                
                edit_helpdesk_subdomain = ""
                if edit_helpdesk_platform != "None":
                    if edit_helpdesk_platform == "Zoho Desk":
                        edit_helpdesk_client_id = st.text_input(f"Edit Zoho Client ID", agent["helpdesk_client_id"], key=f"edit_client_id_{i}")
                        edit_helpdesk_client_secret = st.text_input(f"Edit Zoho Client Secret", agent["helpdesk_client_secret"], type="password", key=f"edit_client_secret_{i}")
                        edit_helpdesk_refresh_token = st.text_input(f"Edit Zoho Refresh Token", agent["helpdesk_refresh_token"], type="password", key=f"edit_refresh_{i}")
                        edit_helpdesk_org_id = st.text_input(f"Edit Zoho Desk Org ID", agent["helpdesk_org_id"], key=f"edit_org_id_{i}")
                        edit_helpdesk_department_id = st.text_input(f"Edit Department ID", agent["helpdesk_department_id"], key=f"edit_dept_id_{i}")
                    else:
                        edit_helpdesk_client_id = st.text_input(f"Edit Zendesk API Key", agent["helpdesk_client_id"], type="password", key=f"edit_client_id_{i}")
                        edit_helpdesk_client_secret = ""
                        edit_helpdesk_refresh_token = ""
                        edit_helpdesk_org_id = ""
                        edit_helpdesk_department_id = st.text_input(f"Edit Department ID", agent["helpdesk_department_id"], key=f"edit_dept_id_{i}")
                        edit_helpdesk_subdomain = st.text_input(f"Edit Zendesk Subdomain", agent["helpdesk_subdomain"], key=f"edit_subdomain_{i}")
                    edit_create_tickets = st.checkbox(f"Edit Create Tickets", value=agent["create_tickets"], key=f"edit_tickets_{i}")
                else:
                    edit_helpdesk_client_id = edit_helpdesk_client_secret = edit_helpdesk_refresh_token = edit_helpdesk_org_id = edit_helpdesk_department_id = ""
                    edit_create_tickets = False
                
                edit_avatar = st.file_uploader(f"Update Avatar for {agent['name']}", type=["png", "jpg", "jpeg"], key=f"edit_avatar_{i}")
                
                if edit_avatar and st.button(f"Upload Avatar for {agent['name']}", key=f"upload_avatar_{i}"):
                    async def upload_avatar_async():
                        form_data = FormData()
                        form_data.add_field("user_id", st.session_state.user.id)
                        form_data.add_field("agent_id", agent["id"])
                        form_data.add_field("file", edit_avatar, filename=edit_avatar.name)
                        async with aiohttp.ClientSession() as session:
                            resp = await session.post("http://localhost:8000/upload_avatar", data=form_data)
                            return await resp.json()
                    result = asyncio.run(upload_avatar_async())
                    if "avatar_url" in result:
                        st.session_state.agents[i]["avatar_url"] = result["avatar_url"]
                        st.success(result.get("message", "Avatar uploaded successfully"))
                    else:
                        st.error("Avatar upload failed. Check logs for details.")
                    st.rerun()
                
                st.subheader(f"RAG Files for {agent['name']}")
                async def list_rag_async():
                    async with aiohttp.ClientSession() as session:
                        resp = await session.get(f"http://localhost:8000/list_rag?user_id={st.session_state.user.id}")
                        return await resp.json() if resp.status == 200 else {"files": []}
                rag_files = asyncio.run(list_rag_async())["files"]
                agent_rag_files = [f for f in rag_files if f["agent_id"] == agent["id"]]
                if agent_rag_files:
                    for file in agent_rag_files:
                        st.write(f"{file['filename']} (Uploaded: {file['upload_date']}) - Path: {file['file_path']}")
                        if st.button(f"Delete {file['filename']}", key=f"delete_rag_{file['id']}_{i}"):
                            async def delete_rag_async():
                                async with aiohttp.ClientSession() as session:
                                    resp = await session.post("http://localhost:8000/delete_rag", json={
                                        "user_id": st.session_state.user.id, "memory_id": file["id"]
                                    })
                                    return await resp.json()
                            result = asyncio.run(delete_rag_async())
                            st.success(result.get("message", f"Deleted {file['filename']} successfully"))
                            st.rerun()
                else:
                    st.write("No RAG files attached.")
                
                new_rag_file = st.file_uploader(f"Add RAG File for {agent['name']}", type=["txt", "pdf"], key=f"new_rag_{i}")
                if new_rag_file and st.button(f"Upload for {agent['name']}", key=f"upload_rag_{i}"):
                    async def upload_async():
                        form_data = FormData()
                        form_data.add_field("user_id", st.session_state.user.id)
                        form_data.add_field("agent_id", agent["id"])
                        form_data.add_field("file", new_rag_file, filename=new_rag_file.name)
                        async with aiohttp.ClientSession() as session:
                            resp = await session.post("http://localhost:8000/upload_rag", data=form_data)
                            return await resp.json()
                    result = asyncio.run(upload_async())
                    st.success(result.get("message", "RAG file uploaded successfully"))
                    st.rerun()
                
                st.subheader(f"Deploy {agent['name']} on Your Website")
                widget_code = f"""<script src="http://localhost:8000/static/widget.js" 
    data-user-id="{st.session_state.user.id}" 
    data-agent-id="{agent['id']}" 
    data-api-key="{agent['api_key']}"></script>"""
                st.text_area(f"Copy this code for {agent['name']}", widget_code, key=f"widget_{agent['id']}", height=100)
                if st.button(f"Copy Code for {agent['name']}", key=f"copy_{i}"):
                    st.write("Code copied! Paste it into your website's HTML.")
                
                if st.button(f"Save Changes for {agent['name']}", key=f"save_{i}"):
                    if not edit_key:
                        st.error("LLM API Key is required for the agent.")
                    elif edit_helpdesk_platform == "Zoho Desk" and not all([edit_helpdesk_client_id, edit_helpdesk_client_secret, edit_helpdesk_refresh_token, edit_helpdesk_org_id]):
                        st.error("Zoho Desk requires Client ID, Client Secret, Refresh Token, and Org ID.")
                    elif edit_helpdesk_platform == "Zendesk" and not all([edit_helpdesk_client_id, edit_helpdesk_subdomain]):
                        st.error("Zendesk requires API Key and Subdomain.")
                    else:
                        updated_agent = {
                            "id": agent["id"],
                            "name": edit_name,
                            "role": edit_role,
                            "llm_type": edit_llm,
                            "api_key": edit_key,
                            "rag_only": True,
                            "info": edit_persona,
                            "company": edit_company,
                            "is_default": False,  # No default agent
                            "avatar_url": agent.get("avatar_url", ""),
                            "department": edit_department,
                            "helpdesk_platform": edit_helpdesk_platform.lower() if edit_helpdesk_platform != "None" else "",
                            "helpdesk_client_id": edit_helpdesk_client_id,
                            "helpdesk_client_secret": edit_helpdesk_client_secret,
                            "helpdesk_refresh_token": edit_helpdesk_refresh_token,
                            "helpdesk_org_id": edit_helpdesk_org_id,
                            "helpdesk_department_id": edit_helpdesk_department_id,
                            "helpdesk_subdomain": edit_helpdesk_subdomain,
                            "create_tickets": edit_create_tickets
                        }
                        async def update_async():
                            async with aiohttp.ClientSession() as session:
                                resp = await session.post("http://localhost:8000/update_agent", json={
                                    "user_id": st.session_state.user.id,
                                    "agent_id": agent["id"],
                                    "agent": updated_agent
                                })
                                if resp.status != 200:
                                    logger.error(f"Update failed: {await resp.text()}")
                                    return {"message": "Update failed", "agent": agent}
                                return await resp.json()
                        result = asyncio.run(update_async())
                        st.session_state.agents[i] = result["agent"]
                        logger.info(f"Updated agent in session state: {result['agent']}")
                        st.success(result.get("message", f"Updated {edit_name} successfully"))
                        st.rerun()
                
                if st.button(f"Remove {agent['name']}", key=f"remove_{i}"):
                    async def delete_async():
                        async with aiohttp.ClientSession() as session:
                            resp = await session.post("http://localhost:8000/delete_agent", json={
                                "user_id": st.session_state.user.id,
                                "agent_id": agent["id"]
                            })
                            return await resp.json()
                    result = asyncio.run(delete_async())
                    st.success(result.get("message", f"Deleted {agent['name']} successfully"))
                    st.session_state.agents.pop(i)
                    st.rerun()

else:
    st.write("Please sign in or sign up to use CogniCrew.")