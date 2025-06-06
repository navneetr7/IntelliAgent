import os
import asyncio
import aiohttp
from fastapi import FastAPI, HTTPException, Form, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import supabase
from sentence_transformers import SentenceTransformer
from datetime import datetime
import logging
import json
from typing import List, Optional
import PyPDF2
import io
from functools import lru_cache
import time
import re  # Added for sanitization
import hashlib  # Added for hashing

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
if not all([supabase_url, supabase_key, deepseek_api_key]):
    logger.error("Missing environment variables")
    raise ValueError("Missing SUPABASE_URL, SUPABASE_KEY, or DEEPSEEK_API_KEY")
supabase_client = supabase.create_client(supabase_url, supabase_key)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class SupabaseVectorStore:
    def __init__(self, client):
        self.client = client
        self.embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    async def _get_embedding(self, text):
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        response = self.client.table("query_embeddings").select("embedding").eq("content_hash", content_hash).execute()
        if response.data:
            return response.data[0]["embedding"]
        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(None, lambda: self.embedder.encode(text).tolist())
        self.client.table("query_embeddings").insert({
            "query": content_hash,
            "content_hash": content_hash,
            "embedding": embedding,
            "timestamp": datetime.now().isoformat()
        }).execute()
        return embedding

    async def _get_file_content(self, file_path):
        try:
            response = self.client.storage.from_("ragfiles").download(file_path)
            if file_path.endswith(".pdf"):
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(response))
                return "".join(page.extract_text() for page in pdf_reader.pages if page.extract_text())
            return response.decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to fetch file content from {file_path}: {e}")
            return ""

    async def search(self, query, user_id, agent_id=None, limit=3):
        logger.info(f"Starting search for query: '{query}', user_id: {user_id}, agent_id: {agent_id}")
        try:
            query_embedding = await self._get_embedding(query)
            response = self.client.rpc(
                "match_rag_files",
                {"query_embedding": query_embedding, "user_id": user_id, "agent_id": agent_id, "match_limit": limit}
            ).execute()
            if not response.data:
                logger.info(f"No matching RAG entries for user_id: {user_id}, agent_id: {agent_id}")
                return []
            results = []
            for r in response.data:
                content = await self._get_file_content(r["file_path"])
                results.append({"content": content, "metadata": r["metadata"]})
            return results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    async def add(self, file_content, user_id, metadata, file_path):
        embedding = await self._get_embedding(file_content)
        response = self.client.table("rag_metadata").insert({
            "user_id": user_id,
            "agent_id": metadata.get("agent_id"),
            "file_path": file_path,
            "metadata": metadata,
            "embedding": embedding
        }).execute()
        return response.data[0]["id"] if response.data else None

async def call_llm(llm_type, api_key, messages):
    start = time.time()
    urls = {
        "deepseek": "https://api.deepseek.com/chat/completions",
        "gpt": "https://api.openai.com/v1/chat/completions",
        "grok": "https://api.xai.com/v1/chat",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    }
    models = {
        "deepseek": "deepseek-chat",
        "gpt": "gpt-3.5-turbo",
        "grok": "grok",
        "gemini": "gemini-2.0-flash"
    }
    url = urls.get(llm_type, "https://api.openai.com/v1/chat/completions")
    model = models.get(llm_type, "gpt-3.5-turbo")
    if not api_key:
        raise HTTPException(status_code=400, detail=f"No API key for {llm_type}")
    
    headers = {"Content-Type": "application/json"}
    if llm_type != "gemini":
        headers["Authorization"] = f"Bearer {api_key}"
    
    if llm_type == "gemini":
        system_prompt = messages[0]["content"] if messages[0]["role"] == "system" else ""
        conversation = "\n\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in messages[1:]])
        payload = {
            "contents": [{"parts": [{"text": f"{system_prompt}\n\n{conversation}"}]}]
        }
        url += f"?key={api_key}"
    else:
        payload = {"model": model, "messages": messages, "temperature": 0.7}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"LLM API error: {error_text}")
                data = await resp.json()
                if llm_type == "gemini":
                    result = data["candidates"][0]["content"]["parts"][0]["text"]
                else:
                    result = data["choices"][0]["message"]["content"]
                logger.info(f"{llm_type} took {time.time() - start:.2f}s")
                return result
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        raise HTTPException(status_code=500, detail=f"LLM call failed: {str(e)}")

class AgentConfig(BaseModel):
    id: Optional[str] = None
    name: str
    role: str
    llm_type: str
    api_key: str
    rag_only: bool = False
    info: str = ""
    company: str = ""
    is_default: bool = False
    avatar_url: Optional[str] = None
    helpdesk_platform: str = ""
    helpdesk_client_id: str = ""
    helpdesk_client_secret: str = ""
    helpdesk_refresh_token: str = ""
    helpdesk_org_id: str = ""
    helpdesk_department_id: str = ""
    helpdesk_subdomain: str = ""
    create_tickets: bool = False
    department: str = ""

class ChatRequest(BaseModel):
    user_id: str
    message: str
    mode: str
    agents: List[AgentConfig]
    history: List[dict] = []
    customer_email: Optional[str] = None
    customer_name: Optional[str] = None
    language: str = "English"
    department: Optional[str] = None  # Added to allow department selection

class DeleteRagRequest(BaseModel):
    user_id: str
    memory_id: str

class WidgetRequest(BaseModel):
    user_id: str
    message: str
    department: str
    api_key: str
    history: List[dict] = []
    customer_email: Optional[str] = None
    customer_name: Optional[str] = None
    language: str = "English"

class TicketRequest(BaseModel):
    user_id: str
    agent_id: str
    message: str
    response: str
    platform: str
    department_id: Optional[str] = None
    customer_email: Optional[str] = None
    customer_name: Optional[str] = None
    agent_name: Optional[str] = None

class ContactSetupRequest(BaseModel):
    user_id: str
    agent_id: Optional[str] = None
    name: str
    email: str

async def get_zoho_access_token(client_id: str, client_secret: str, refresh_token: str):
    url = "https://accounts.zoho.in/oauth/v2/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "scope": "Desk.tickets.ALL,Desk.contacts.CREATE"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload) as resp:
            response_text = await resp.text()
            logger.info(f"Zoho token refresh response: status={resp.status}, body={response_text}")
            if resp.status != 200:
                logger.error(f"Failed to refresh Zoho token: {response_text}")
                raise HTTPException(status_code=resp.status, detail=f"Failed to refresh Zoho token: {response_text}")
            data = await resp.json()
            if "access_token" not in data:
                logger.error(f"No 'access_token' in Zoho response: {data}")
                raise HTTPException(status_code=400, detail=f"Invalid Zoho response: {data}")
            logger.info(f"Generated access_token: {data['access_token'][:10]}... (scope: {data.get('scope', 'unknown')})")
            return data["access_token"]

async def create_zoho_contact(access_token: str, org_id: str, email: str, name: str):
    url = "https://desk.zoho.in/api/v1/contacts"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json",
        "orgId": org_id
    }
    payload = {
        "email": email,
        "lastName": name.split()[-1] if " " in name else name,
        "firstName": name.split()[0] if " " in name else ""
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            logger.info(f"Creating contact: URL={url}, Headers={headers}, Payload={payload}")
            if resp.status in [200, 201]:
                contact = await resp.json()
                contact_id = contact["id"]
                logger.info(f"Created new contact: {contact_id} for email: {email}")
                return contact_id
            else:
                error_text = await resp.text()
                logger.error(f"Contact creation failed: status={resp.status}, error={error_text}")
                raise HTTPException(status_code=resp.status, detail=f"Contact creation failed: {error_text}")

@app.post("/setup_contact")
async def setup_contact(request: ContactSetupRequest):
    try:
        if request.agent_id:
            agent_response = supabase_client.table("agents").select(
                "helpdesk_client_id, helpdesk_client_secret, helpdesk_refresh_token, helpdesk_org_id, helpdesk_platform, name"
            ).eq("id", request.agent_id).eq("user_id", request.user_id).execute()
            if not agent_response.data:
                raise HTTPException(status_code=404, detail="Agent not found")
            agent_data = agent_response.data[0]
        else:
            agent_data = None

        if agent_data and agent_data.get("helpdesk_platform") == "zoho desk":
            client_id = agent_data.get("helpdesk_client_id")
            client_secret = agent_data.get("helpdesk_client_secret")
            refresh_token = agent_data.get("helpdesk_refresh_token")
            org_id = agent_data.get("helpdesk_org_id")
            
            access_token = await get_zoho_access_token(client_id, client_secret, refresh_token)
            contact_id = await create_zoho_contact(access_token, org_id, request.email, request.name)
            return {"contact_id": contact_id}
        return {"contact_id": None}
    except Exception as e:
        logger.error(f"Contact setup failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to set up contact: {str(e)}")

@app.post("/chat")
async def chat(request: ChatRequest):
    vector_store = SupabaseVectorStore(supabase_client)
    normalized_message = request.message.lower().strip()
    
    responses = []
    rag_text = ""
    try:
        if request.mode == "single_rag":
            # Use department to select agent if provided, otherwise take first agent
            if request.department:
                agent = next((a for a in request.agents if a.department == request.department), None)
                if not agent:
                    raise HTTPException(status_code=404, detail=f"No agent found for department: {request.department}")
            else:
                agent = request.agents[0]  # Fallback to first agent if no department specified
            
            company_name = agent.company if agent.company else "CogniCrew"
            
            rag_task = vector_store.search(normalized_message, request.user_id, agent_id=agent.id)
            prompt = f"""
            You’re {agent.name} at {company_name}, in the {agent.department} department. Your persona: {agent.info}.
            - Do NOT prepend your name, 'assistant:', 'agent:', or any role-based prefix to your response; provide only the raw message content with no labels.
            - If the user asks "What is your name?" "Who are you?" or similar, respond naturally (e.g., "I’m {agent.name}! How can I help you today?").
            - Start by responding in {request.language}.
            - If the user says "Switch to [language]" or similar (e.g., "Use Spanish"), detect the requested language (even with typos), switch to it for all future responses, and confirm: "Switched to [language]!"
            - If the conversation history is empty (length 0), it’s the first message—end with: "By the way, need a different language? Just say 'Switch to Spanish,' 'Switch to French,' etc."—otherwise, do not include this unless the user mentions languages.
            - Respond to casual greetings creatively and variably only if the input is clearly a greeting and not a question:
              - "Hello" or "Hi": "Hi there! What’s on your mind today?"
              - "Hey" or "Heya": "Heya! Good to chat—what can I do for you?"
              - "How’s it going": "Hey, doing great—how about you? What’s up?"
            - For any input resembling a question (e.g., starts with 'what', 'how', 'why', or contains '?'), answer directly without a greeting unless it’s the first message.
            - For anything about {company_name}:
              1. Use this data ONLY: {{rag_text}}. Quote it exactly—no paraphrasing, no guessing.
              2. Guess their setup or pain points based on prior messages if available.
              3. Give the fix or info naturally, like you’re recalling it.
              4. If the data mentions packages, upsell once—explain the perk, ask if they’re interested, then drop it unless they bring it up again.
            - If no data: Say: “Sorry, I don’t have specific info on that. Check {company_name}’s site or let me know how else I can help!”
            - Off-topic? Say: “I’m here to assist with any {company_name}-related questions or support needs. How can I help you today?”—keep it simple unless it’s the first message.
            - Be friendly, helpful, and on-brand—never bash {company_name}.
            - Use the conversation history to stay consistent, vary responses, and avoid repetition unless necessary.
            """
            rag_context, _ = await asyncio.gather(rag_task, asyncio.sleep(0))
            rag_text = "\n".join([r["content"] for r in rag_context]) if rag_context else "No relevant data found."
            prompt = prompt.replace("{rag_text}", rag_text)
            
            messages = [{"role": "system", "content": prompt}] + request.history + [{"role": "user", "content": request.message}]
            raw_response = await call_llm(agent.llm_type, agent.api_key, messages)
            
            sanitized_response = re.sub(
                r"^(assistant|agent|\w+):(\s+)?", "", raw_response.strip(), flags=re.IGNORECASE
            ).strip()
            
            if not sanitized_response:
                sanitized_response = "Sorry, I didn’t catch that. How can I assist you?"
            
            formatted_response = f"{agent.name}: {sanitized_response}"
            
            responses.append({
                "agent": agent.name,
                "response": formatted_response,
                "avatar_url": agent.avatar_url
            })
        logger.info(f"Raw LLM response: {raw_response}")
        logger.info(f"Sanitized response: {sanitized_response}")
        logger.info(f"Formatted response: {formatted_response}")
        logger.info(f"Query: {normalized_message}, Agent: {agent.name} (ID: {agent.id}), RAG: {rag_text}, Language: {request.language}")
        logger.info(f"Chat response: {responses}")
        return {"responses": responses, "rag_context": rag_text}
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

@app.post("/upload_rag")
async def upload_rag(user_id: str = Form(...), agent_id: str = Form(...), file: UploadFile = File(...)):
    vector_store = SupabaseVectorStore(supabase_client)
    try:
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File exceeds 10MB limit")
        
        file_path = f"{user_id}/{agent_id}/{datetime.now().isoformat()}_{file.filename}"
        supabase_client.storage.from_("ragfiles").upload(file_path, content)
        
        content_str = content.decode("utf-8") if not file.filename.endswith(".pdf") else \
                      "".join(PyPDF2.PdfReader(io.BytesIO(content)).pages[i].extract_text() 
                              for i in range(len(PyPDF2.PdfReader(io.BytesIO(content)).pages)))
        
        metadata = {
            "filename": file.filename,
            "upload_date": datetime.now().isoformat(),
            "type": "rag",
            "agent_id": agent_id
        }
        memory_id = await vector_store.add(content_str, user_id, metadata, file_path)
        logger.info(f"Uploaded {file.filename} to Storage at {file_path} for agent_id {agent_id}")
        return {"message": f"Uploaded {file.filename} successfully", "memory_id": memory_id}
    except UnicodeDecodeError as e:
        logger.error(f"Decode error for {file.filename}: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid file encoding: {str(e)}")
    except Exception as e:
        logger.error(f"Upload failed for {file.filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload {file.filename}: {str(e)}")

@app.post("/upload_avatar")
async def upload_avatar(user_id: str = Form(...), agent_id: str = Form(...), file: UploadFile = File(...)):
    try:
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="File must be an image")
        if file.size > 2 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image exceeds 2MB limit")
        
        content = await file.read()
        file_name = f"{agent_id}_{datetime.now().isoformat()}_{file.filename}"
        response = supabase_client.storage.from_("avatars").upload(file_name, content, {"content-type": file.content_type})
        
        public_url = supabase_client.storage.from_("avatars").get_public_url(file_name)
        supabase_client.table("agents").update({"avatar_url": public_url}).eq("id", agent_id).eq("user_id", user_id).execute()
        
        logger.info(f"Uploaded avatar for agent {agent_id}")
        return {"message": "Avatar uploaded successfully", "avatar_url": public_url}
    except Exception as e:
        logger.error(f"Avatar upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload avatar: {str(e)}")

@app.get("/list_rag")
async def list_rag(user_id: str):
    try:
        response = supabase_client.table("rag_metadata").select("id, file_path, metadata").eq("user_id", user_id).execute()
        files = [{"id": r["id"], "filename": r["metadata"].get("filename"), "agent_id": r["metadata"].get("agent_id"), "upload_date": r["metadata"].get("upload_date"), "file_path": r["file_path"]} for r in response.data]
        return {"files": files}
    except Exception as e:
        logger.error(f"List RAG failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list RAG files: {str(e)}")

@app.post("/delete_rag")
async def delete_rag(request: DeleteRagRequest):
    try:
        response = supabase_client.table("rag_metadata").select("file_path").eq("id", request.memory_id).eq("user_id", request.user_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="RAG file not found")
        file_path = response.data[0]["file_path"]
        
        supabase_client.storage.from_("ragfiles").remove([file_path])
        supabase_client.table("rag_metadata").delete().eq("id", request.memory_id).eq("user_id", request.user_id).execute()
        
        logger.info(f"Deleted RAG file {request.memory_id} from Storage at {file_path}")
        return {"message": f"Deleted RAG file {request.memory_id} successfully"}
    except Exception as e:
        logger.error(f"Delete RAG failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete RAG file: {str(e)}")

@app.post("/add_agent")
async def add_agent(request: dict):
    try:
        user_id = request["user_id"]
        agent = AgentConfig(**request["agent"])
        data = agent.dict(exclude={"id"})
        data["user_id"] = user_id
        response = supabase_client.table("agents").insert(data).execute()
        logger.info(f"Added agent {agent.name} for user {user_id}")
        return {"message": f"Added {agent.name} successfully", "agent": response.data[0]}
    except Exception as e:
        logger.error(f"Add agent failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/list_agents")
async def list_agents(user_id: str):
    try:
        response = supabase_client.table("agents").select("*").eq("user_id", user_id).execute()
        return {"agents": response.data}
    except Exception as e:
        logger.error(f"List agents failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update_agent")
async def update_agent(request: dict):
    try:
        user_id = request["user_id"]
        agent_id = request["agent_id"]
        agent_data = request["agent"]
        response = supabase_client.table("agents").update(agent_data).eq("id", agent_id).eq("user_id", user_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Agent not found")
        updated_agent = response.data[0]
        logger.info(f"Updated agent {updated_agent['name']} (ID: {agent_id}) with llm_type: {updated_agent['llm_type']}, api_key: {updated_agent['api_key'][:4]}...")
        return {"message": f"Updated {updated_agent['name']} successfully", "agent": updated_agent}
    except Exception as e:
        logger.error(f"Update agent failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/delete_agent")
async def delete_agent(request: dict):
    try:
        user_id = request["user_id"]
        agent_id = request["agent_id"]
        response = supabase_client.table("agents").delete().eq("id", agent_id).eq("user_id", user_id).execute()
        logger.info(f"Deleted agent {agent_id}")
        return {"message": f"Deleted agent {agent_id} successfully"}
    except Exception as e:
        logger.error(f"Delete agent failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat_widget")
async def chat_widget(request: WidgetRequest):
    vector_store = SupabaseVectorStore(supabase_client)
    normalized_message = request.message.lower().strip()
    
    # Find agent by department (no default agent logic)
    agent_response = supabase_client.table("agents").select("*").eq("user_id", request.user_id).eq("department", request.department).execute()
    if not agent_response.data:
        raise HTTPException(status_code=404, detail=f"No agent found for department: {request.department}")
    agent_raw = agent_response.data[0]  # Take first match
    
    agent = {
        "name": agent_raw.get("name", "Unnamed Agent"),
        "company": agent_raw.get("company", "CogniCrew"),
        "id": agent_raw.get("id"),
        "llm_type": agent_raw.get("llm_type", "deepseek"),
        "api_key": agent_raw.get("api_key", ""),
        "avatar_url": agent_raw.get("avatar_url"),
        "helpdesk_platform": agent_raw.get("helpdesk_platform", ""),
        "helpdesk_client_id": agent_raw.get("helpdesk_client_id", ""),
        "helpdesk_client_secret": agent_raw.get("helpdesk_client_secret", ""),
        "helpdesk_refresh_token": agent_raw.get("helpdesk_refresh_token", ""),
        "helpdesk_org_id": agent_raw.get("helpdesk_org_id", ""),
        "helpdesk_department_id": agent_raw.get("helpdesk_department_id", ""),
        "helpdesk_subdomain": agent_raw.get("helpdesk_subdomain", ""),
        "create_tickets": agent_raw.get("create_tickets", False),
        "info": agent_raw.get("info", ""),
        "department": agent_raw.get("department", "")
    }
    
    if agent["api_key"] != request.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    
    rag_context = await vector_store.search(normalized_message, request.user_id, agent_id=agent["id"])
    rag_text = "\n".join([r["content"] for r in rag_context]) if rag_context else "No relevant data found."
    
    prompt = f"""
    You’re {agent['name']} at {agent['company']}, in the {agent['department']} department. Your persona: {agent['info']}.
    - Do NOT prepend your name, 'assistant:', 'agent:', or any role-based prefix to your response; provide only the raw message content with no labels.
    - If the user asks "What is your name?" "Who are you?" or similar, respond naturally (e.g., "I’m {agent['name']}! How can I help you today?").
    - Start by responding in {request.language}.
    - If the user says "Switch to [language]" or similar (e.g., "Use Spanish"), detect the requested language (even with typos), switch to it for all future responses, and confirm: "Switched to [language]!"
    - If the conversation history is empty (length 0), it’s the first message—end with: "By the way, need a different language? Just say 'Switch to Spanish,' 'Switch to French,' etc."—otherwise, do not include this unless the user mentions languages.
    - Respond to casual greetings creatively and variably only if the input is clearly a greeting and not a question:
      - "Hello" or "Hi": "Hi there! What’s on your mind today?"
      - "Hey" or "Heya": "Heya! Good to chat—what can I do for you?"
      - "How’s it going": "Hey, doing great—how about you? What’s up?"
    - For any input resembling a question (e.g., starts with 'what', 'how', 'why', or contains '?'), answer directly without a greeting unless it’s the first message.
    - For anything about {agent['company']}:
      1. Use this data ONLY: {rag_text}. Quote it exactly—no paraphrasing, no guessing.
      2. Guess their setup or pain points based on prior messages if available.
      3. Give the fix or info naturally, like you’re recalling it.
      4. If the data mentions packages, upsell once—explain the perk, ask if they’re interested, then drop it unless they bring it up again.
    - If no data: Say: “Sorry, I don’t have specific info on that. Check {agent['company']}’s site or let me know how else I can help!”
    - Off-topic? Say: “I’m here to assist with any {agent['company']}-related questions or support needs. How can I help you today?”—keep it simple unless it’s the first message.
    - Be friendly, helpful, and on-brand—never bash {agent['company']}.
    - Use the conversation history to stay consistent, vary responses, and avoid repetition unless necessary.
    """
    messages = [{"role": "system", "content": prompt}] + request.history + [{"role": "user", "content": request.message}]
    raw_response = await call_llm(agent["llm_type"], agent["api_key"], messages)
    
    sanitized_response = re.sub(
        r"^(assistant|agent|\w+):(\s+)?", "", raw_response.strip(), flags=re.IGNORECASE
    ).strip()
    
    if not sanitized_response:
        sanitized_response = "Sorry, I didn’t catch that. How can I assist you?"
    
    formatted_response = f"{agent['name']}: {sanitized_response}"
    
    logger.info(f"Raw LLM response: {raw_response}")
    logger.info(f"Sanitized response: {sanitized_response}")
    logger.info(f"Formatted response: {formatted_response}")
    
    return {
        "agent": agent["name"],
        "response": formatted_response,
        "avatar_url": agent["avatar_url"],
        "agent_id": agent["id"],
        "ticket_id": None
    }

@app.post("/create_ticket")
async def create_ticket(request: TicketRequest):
    try:
        # Fetch agent by agent_id directly, no default fallback
        agent_response = supabase_client.table("agents").select(
            "helpdesk_client_id, helpdesk_client_secret, helpdesk_refresh_token, helpdesk_org_id, helpdesk_department_id, helpdesk_subdomain, name, helpdesk_platform"
        ).eq("id", request.agent_id).eq("user_id", request.user_id).execute()
        if not agent_response.data:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        agent_data = agent_response.data[0]
        client_id = agent_data.get("helpdesk_client_id")
        client_secret = agent_data.get("helpdesk_client_secret")
        refresh_token = agent_data.get("helpdesk_refresh_token")
        org_id = agent_data.get("helpdesk_org_id")
        department_id = agent_data.get("helpdesk_department_id")
        subdomain = agent_data.get("helpdesk_subdomain")
        agent_name = agent_data.get("name")
        helpdesk_platform = agent_data.get("helpdesk_platform", "")

        customer_email = request.customer_email or "anonymous@example.com"
        customer_name = request.customer_name or "Anonymous"
        logger.info(f"Creating ticket for email: {customer_email}, name: {customer_name}, agent: {agent_name}")

        subject = f"Assisted by {request.agent_name or agent_name}" if (request.agent_name or agent_name) else request.message[:50] + "..."

        conversation_lines = request.response.split("\n")
        formatted_conversation = []
        last_speaker = None
        for line in conversation_lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("User:"):
                cleaned_line = line.replace("User:", "").strip()
                formatted_conversation.append(f"{customer_name}: {cleaned_line}")
                last_speaker = customer_name
            elif line.startswith("Agent:") or line.startswith("Assistant:") or line.startswith(f"{agent_name}:"):
                cleaned_line = line.replace("Agent:", "").replace("Assistant:", "").replace(f"{agent_name}:", "").strip()
                if last_speaker != agent_name:
                    formatted_conversation.append(f"{agent_name}: {cleaned_line}")
                else:
                    formatted_conversation.append(cleaned_line)
                last_speaker = agent_name
            else:
                formatted_conversation.append(line)
                last_speaker = None

        description = "\n".join(formatted_conversation) or f"User: {request.message}\nAgent: {request.response}"
        logger.info(f"Formatted ticket description:\n{description}")

        if request.platform.lower() == "zoho desk":
            if not all([client_id, client_secret, refresh_token, org_id]):
                raise HTTPException(status_code=400, detail="Zoho Desk requires Client ID, Client Secret, Refresh Token, and Org ID.")
            
            access_token = await get_zoho_access_token(client_id, client_secret, refresh_token)
            contact_id = await create_zoho_contact(access_token, org_id, customer_email, customer_name)
            
            url = "https://desk.zoho.in/api/v1/tickets"
            headers = {
                "Authorization": f"Zoho-oauthtoken {access_token}",
                "Content-Type": "application/json",
                "orgId": org_id
            }
            payload = {
                "subject": subject,
                "description": description,
                "contactId": contact_id,
                "departmentId": department_id if department_id else request.department_id,
                "status": "Open",
                "priority": "Medium"
            }
        elif request.platform.lower() == "zendesk":
            if not subdomain:
                raise HTTPException(status_code=400, detail="Zendesk subdomain required for this agent.")
            api_key = client_id
            if not api_key:
                raise HTTPException(status_code=400, detail="Zendesk API key required.")
            url = f"https://{subdomain}.zendesk.com/api/v2/tickets"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "ticket": {
                    "subject": subject,
                    "comment": {"body": description},
                    "requester": {"name": customer_name, "email": customer_email},
                    "department_id": department_id if department_id else request.department_id
                }
            }
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported platform: {request.platform}")

        logger.info(f"Sending ticket to {request.platform}: URL={url}, Headers={headers}, Payload={payload}")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status not in [200, 201]:
                    error_text = await resp.text()
                    logger.error(f"Ticket API response: status={resp.status}, error={error_text}")
                    raise HTTPException(status_code=resp.status, detail=f"Ticket creation failed: {error_text}")
                ticket = await resp.json()
                ticket_id = ticket.get("ticketNumber") if request.platform.lower() == "zoho desk" else ticket.get("id")
                logger.info(f"Ticket created successfully: {ticket}")
                return {"message": f"Ticket created: {ticket_id}", "ticket_id": ticket_id}
    except Exception as e:
        logger.error(f"Ticket creation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create ticket: {str(e)}")