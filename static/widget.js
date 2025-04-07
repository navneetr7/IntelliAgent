(function() {
    // Global registry for agent IDs from all script tags
    if (!window.cogniCrewWidgetAgents) {
        window.cogniCrewWidgetAgents = new Set();
    }
    const agentRegistry = window.cogniCrewWidgetAgents;

    // Get configuration from current script tag
    const scriptTag = document.currentScript || document.querySelector('script[src*="widget.js"]');
    const config = {
        userId: scriptTag?.getAttribute("data-user-id"),
        apiKey: scriptTag?.getAttribute("data-api-key"),
        agentId: scriptTag?.getAttribute("data-agent-id")
    };
    console.log(`Script tag config:`, config);

    if (!config.userId || !config.apiKey || !config.agentId) {
        console.error(`Missing required config in script tag: userId, apiKey, or agentId`);
        return; // Exit if config is incomplete
    }

    // Add agent ID to global registry
    agentRegistry.add(config.agentId);

    // Only initialize the widget once, after all script tags are processed
    if (!window.cogniCrewWidgetInitialized) {
        window.cogniCrewWidgetInitialized = true;

        const instanceId = `widget_${Math.random().toString(36).substr(2, 9)}`;
        console.log(`Widget instance ${instanceId} loaded - Version 26 (Dynamic Department Selection)`);

        // Instance-specific state
        const state = {
            agentInfo: { name: null, avatar: null, id: null }, // Will be set based on selected department
            customerInfo: { name: null, email: null, language: "English", department: null },
            sessionMessages: [],
            inactivityTimer: null,
            agents: [],
            departments: []
        };
        const INACTIVITY_TIMEOUT = 5 * 60 * 1000; // 5 minutes

        // Load previous session from localStorage (per user, not per agent)
        const chatHistoryKey = `chat_${config.userId}`;
        let persistedMessages = JSON.parse(localStorage.getItem(chatHistoryKey)) || [];
        if (persistedMessages.length > 0) {
            const savedCustomer = persistedMessages.find(msg => msg.customerName && msg.customerEmail);
            if (savedCustomer) {
                state.customerInfo.name = savedCustomer.customerName;
                state.customerInfo.email = savedCustomer.customerEmail;
                state.customerInfo.language = savedCustomer.language || "English";
                state.customerInfo.department = savedCustomer.department || null;
            }
            const agentMessage = persistedMessages.find(msg => msg.role === "agent" && msg.agentName);
            if (agentMessage) {
                state.agentInfo.name = agentMessage.agentName;
                state.agentInfo.avatar = agentMessage.avatarUrl;
                state.agentInfo.id = agentMessage.agentId;
            }
        }

        // Add Inter font
        const fontLink = document.createElement("link");
        fontLink.href = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap";
        fontLink.rel = "stylesheet";
        document.head.appendChild(fontLink);

        // --- SVGs for Icons ---
        const ICONS = {
            chat: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 2H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h5l3 3 3-3h5a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/><line x1="7" y1="9" x2="17" y2="9"/><line x1="7" y1="13" x2="13" y2="13"/></svg>`,
            clear: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`,
            send: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="16" x2="16" y2="8"/><line x1="16" y1="8" x2="12" y2="8"/><line x1="16" y1="8" x2="16" y2="12"/></svg>`
        };

        // Define styles
        const styleTag = document.createElement("style");
        styleTag.textContent = `
            .chat-widget-actions-container-${instanceId} {
                position: fixed;
                bottom: 20px;
                right: 20px;
                z-index: 9999;
            }

            .chat-widget-action-button-${instanceId} {
                width: 52px;
                height: 52px;
                border-radius: 50%;
                background: linear-gradient(135deg, #8054A1, #6A4A8E);
                color: white;
                border: none;
                cursor: pointer;
                box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                transition: transform 0.2s ease-out, box-shadow 0.2s ease-out;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 0;
                overflow: hidden;
            }

            .chat-widget-action-button-${instanceId}:hover {
                transform: scale(1.1);
                box-shadow: 0 6px 16px rgba(0,0,0,0.4);
            }
            .chat-widget-action-button-${instanceId}:active {
                transform: scale(1.0);
            }

            .chat-widget-action-button-${instanceId} svg {
                width: 24px;
                height: 24px;
                stroke-width: 2;
            }

            .chat-widget-window-${instanceId} {
                position: fixed;
                bottom: 85px;
                right: 20px;
                width: 400px;
                height: 600px;
                background: #FFFFFF;
                border-radius: 16px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                display: flex;
                flex-direction: column;
                opacity: 0;
                transform: scale(0.9) translateY(20px);
                transition: opacity 0.4s cubic-bezier(0.25, 0.8, 0.25, 1), transform 0.4s cubic-bezier(0.25, 0.8, 0.25, 1);
                overflow: hidden;
                font-family: 'Inter', sans-serif;
                z-index: 9998;
            }

            .chat-widget-window-visible-${instanceId} {
                opacity: 1;
                transform: scale(1) translateY(0);
            }

            .chat-widget-header-${instanceId} {
                padding: 16px;
                background: #8054A1;
                color: white;
                border-radius: 16px 16px 0 0;
                display: flex;
                align-items: center;
                justify-content: space-between;
                height: 70px;
                flex-shrink: 0;
                cursor: move;
                position: relative;
            }

            .chat-widget-close-button-${instanceId} {
                background: transparent;
                border: none;
                color: white;
                font-size: 18px;
                cursor: pointer;
                padding: 4px 8px;
                border-radius: 4px;
                transition: background-color 0.2s;
            }
            .chat-widget-close-button-${instanceId}:hover {
                background-color: rgba(255,255,255,0.1);
            }

            .chat-widget-agent-avatar-${instanceId} { width: 45px; height: 45px; border-radius: 50%; overflow: hidden; background: #6A4A8E; margin-right: 12px; }
            .chat-widget-avatar-img-${instanceId} { width: 100%; height: 100%; object-fit: cover; }
            .chat-widget-agent-info-${instanceId} { flex-grow: 1; display: flex; flex-direction: column; }
            .chat-widget-agent-name-${instanceId} { font-weight: 600; font-size: 16px; margin-bottom: 4px; }
            .chat-widget-status-${instanceId} { display: flex; align-items: center; font-size: 13px; }
            .chat-widget-status-dot-${instanceId} { width: 8px; height: 8px; border-radius: 50%; background: #4CAF50; margin-right: 6px; }
            .chat-widget-messages-${instanceId} { flex-grow: 1; overflow-y: auto; padding: 16px; background: #F5F5F5; display: flex; flex-direction: column; scroll-behavior: smooth; }
            .chat-widget-footer-${instanceId} { background: #FFFFFF; border-top: 1px solid #EEEEEE; display: flex; flex-direction: column; flex-shrink: 0; }
            .chat-widget-footer-actions-${instanceId} { padding: 8px 12px; display: flex; justify-content: flex-end; align-items: center; gap: 10px; }
            .chat-widget-footer-button-${instanceId} { background: #8054A1; color: white; border: none; border-radius: 20px; padding: 6px 12px; font-family: 'Inter', sans-serif; font-size: 13px; cursor: pointer; transition: background-color 0.2s; display: flex; align-items: center; gap: 4px; }
            .chat-widget-footer-button-${instanceId}:hover { background-color: #6A4A8E; }
            .chat-widget-footer-button-${instanceId}:disabled { background-color: #B39DCA; cursor: not-allowed; }
            .chat-widget-footer-button-${instanceId} svg { width: 18px; height: 18px; }
            .chat-widget-input-container-${instanceId} { padding: 12px; display: flex; align-items: center; }
            .chat-widget-input-${instanceId} { width: 100%; padding: 12px; background: #F5F5F5; color: #333333; border: none; border-radius: 24px; outline: none; font-family: 'Inter', sans-serif; font-size: 14px; }
            .chat-widget-input-${instanceId}:disabled { background: #E0E0E0; cursor: not-allowed; }
            .chat-widget-input-${instanceId}:focus { box-shadow: 0 0 0 2px rgba(128, 84, 161, 0.3); }
            .chat-widget-input-actions-${instanceId} { display: flex; align-items: center; margin-left: 8px; }
            .chat-widget-attach-button-${instanceId} { background: transparent; color: #8054A1; border: none; border-radius: 50%; width: 34px; height: 34px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background-color 0.2s; }
            .chat-widget-attach-button-${instanceId}:hover { background-color: rgba(128, 84, 161, 0.1); }
            .chat-widget-send-button-${instanceId} { background: #8054A1; color: white; border: none; border-radius: 50%; width: 34px; height: 34px; cursor: pointer; margin-left: 8px; display: flex; align-items: center; justify-content: center; transition: transform 0.2s, background-color 0.2s; }
            .chat-widget-send-button-${instanceId}:disabled { background: #B39DCA; cursor: not-allowed; }
            .chat-widget-send-button-${instanceId}:hover:not(:disabled) { transform: scale(1.1); background-color: #6A4A8E; }
            .chat-widget-message-${instanceId} { margin: 4px 0; max-width: 75%; word-break: break-word; opacity: 0; transform: translateY(10px); transition: opacity 0.3s, transform 0.3s; animation-duration: 0.3s; animation-fill-mode: forwards; white-space: pre-line; }
            .chat-widget-message-incoming-${instanceId} { opacity: 1; transform: translateY(0); animation-name: message-pop-in-${instanceId}; }
            @keyframes message-pop-in-${instanceId} { 0% { opacity: 0; transform: translateY(10px); } 70% { opacity: 1; transform: translateY(-2px); } 100% { opacity: 1; transform: translateY(0); } }
            .chat-widget-message-user-${instanceId} { align-self: flex-end; background: #E3F2FD; color: #333333; border-radius: 18px 18px 4px 18px; padding: 12px 16px; }
            .chat-widget-message-agent-${instanceId} { align-self: flex-start; background: #F0E6F8; color: #333333; border-radius: 18px 18px 18px 4px; padding: 12px 16px; }
            .chat-widget-message-system-${instanceId} { align-self: center; background: #FFF3E0; color: #E65100; border-radius: 24px; padding: 8px 16px; font-size: 13px; text-align: center; margin: 10px 0; }
            .chat-widget-message-name-${instanceId} { font-size: 12px; color: #8054A1; font-weight: 600; margin-bottom: 4px; }
            .chat-widget-message-content-${instanceId} { display: flex; flex-direction: column; white-space: pre-line; }
            .chat-widget-message-${instanceId} b, .chat-widget-message-${instanceId} strong, .chat-widget-message-content-${instanceId} b, .chat-widget-message-content-${instanceId} strong { font-weight: 600; }
            .chat-widget-message-${instanceId} i, .chat-widget-message-${instanceId} em, .chat-widget-message-content-${instanceId} i, .chat-widget-message-content-${instanceId} em { font-style: italic; }
            .chat-widget-typing-indicator-${instanceId} { align-self: flex-start; display: flex; align-items: center; margin: 4px 0; opacity: 1; transform: translateY(0); transition: opacity 0.3s, transform 0.3s; }
            .chat-widget-typing-bubbles-${instanceId} { background: #F0E6F8; padding: 12px 16px; border-radius: 18px 18px 18px 4px; display: flex; align-items: center; }
            .chat-widget-typing-dot-${instanceId} { width: 8px; height: 8px; border-radius: 50%; background: #8054A1; margin: 0 2px; animation: typing-bubble-${instanceId} 1.4s infinite; opacity: 0.7; }
            .chat-widget-typing-dot-${instanceId}:nth-child(1) { animation-delay: 0s; }
            .chat-widget-typing-dot-${instanceId}:nth-child(2) { animation-delay: 0.2s; }
            .chat-widget-typing-dot-${instanceId}:nth-child(3) { animation-delay: 0.4s; }
            @keyframes typing-bubble-${instanceId} { 0%, 100% { transform: translateY(0px); } 50% { transform: translateY(-6px); } }
            .chat-widget-fade-out-${instanceId} { opacity: 0; transform: translateY(10px); transition: opacity 0.3s, transform 0.3s; }
            .chat-widget-hidden-${instanceId} { display: none !important; }
            .chat-widget-prechat-container-${instanceId} { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
            .chat-widget-prechat-input-${instanceId} { width: 100%; padding: 12px; background: #F5F5F5; color: #333333; border: none; border-radius: 8px; outline: none; font-family: 'Inter', sans-serif; font-size: 14px; }
            .chat-widget-prechat-input-${instanceId}:focus { box-shadow: 0 0 0 2px rgba(128, 84, 161, 0.3); }
            .chat-widget-prechat-select-${instanceId} { width: 100%; padding: 12px; background: #F5F5F5; color: #333333; border: none; border-radius: 8px; outline: none; font-family: 'Inter', sans-serif; font-size: 14px; }
            .chat-widget-prechat-select-${instanceId}:focus { box-shadow: 0 0 0 2px rgba(128, 84, 161, 0.3); }
            .chat-widget-prechat-button-${instanceId} { background: #8054A1; color: white; border: none; border-radius: 8px; padding: 12px; font-family: 'Inter', sans-serif; font-size: 14px; cursor: pointer; transition: background-color 0.2s; }
            .chat-widget-prechat-button-${instanceId}:hover { background-color: #6A4A8E; }
            .chat-widget-prechat-button-${instanceId}:disabled { background-color: #B39DCA; cursor: not-allowed; }
        `;
        document.head.appendChild(styleTag);

        // --- Element References ---
        const elements = {
            chatWindow: null,
            header: null,
            messages: null,
            footer: null,
            footerActions: null,
            input: null,
            sendButton: null,
            clearButton: null,
            closeButton: null,
            agentAvatar: null,
            agentName: null,
            actionsContainer: null,
            toggleButton: null,
            prechatContainer: null,
            chatContainer: null
        };

        // --- State Management ---
        let isWaitingForAgent = false;
        let isChatStarted = state.customerInfo.name && state.customerInfo.email && state.customerInfo.language && state.customerInfo.department;

        // Language options (consistent with main.py)
        const LANGUAGE_OPTIONS = [
            "English", "Spanish", "French", "German", "Italian",
            "Portuguese", "Russian", "Chinese", "Japanese", "Korean"
        ];

        // Fetch agent details for all registered agent IDs
        async function fetchAgentDetails() {
            try {
                const response = await fetch(`http://localhost:8000/list_agents?user_id=${config.userId}`);
                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`Failed to fetch agents: ${response.status} - ${errorText}`);
                }
                const data = await response.json();
                state.agents = data.agents || [];
                console.log(`[${instanceId}] All agents fetched:`, state.agents);

                // Filter agents to only those in the registry
                state.agents = state.agents.filter(agent => agentRegistry.has(agent.id));
                if (state.agents.length === 0) {
                    console.error(`[${instanceId}] No agents found matching registered IDs:`, agentRegistry);
                    state.departments = ["General"];
                    return;
                }

                // Populate departments from filtered agents
                state.departments = [...new Set(state.agents.map(agent => agent.department || "General"))];
                console.log(`[${instanceId}] Departments set:`, state.departments);

                // Set initial agentInfo to the first agent (will update on department selection)
                const initialAgent = state.agents[0];
                state.agentInfo.name = initialAgent.name;
                state.agentInfo.avatar = initialAgent.avatar_url;
                state.agentInfo.id = initialAgent.id;
                state.customerInfo.department = initialAgent.department || "General";
            } catch (error) {
                console.error(`[${instanceId}] Error fetching agent details:`, error);
                state.agents = [];
                state.departments = ["General"];
            }
        }

        // --- Core Functions ---

        function toggleChatWindow() {
            if (!elements.chatWindow) {
                createChatWindow();
                loadDepartments();
                loadMessages();
                setupEventListeners();
                elements.toggleButton.title = "Close Chat";
            } else if (elements.chatWindow.classList.contains(`chat-widget-window-visible-${instanceId}`)) {
                elements.chatWindow.classList.remove(`chat-widget-window-visible-${instanceId}`);
                setTimeout(() => {
                    elements.chatWindow.classList.add(`chat-widget-hidden-${instanceId}`);
                }, 400);
                elements.toggleButton.title = "Open Chat";
            } else {
                elements.chatWindow.classList.remove(`chat-widget-hidden-${instanceId}`);
                setTimeout(() => {
                    elements.chatWindow.classList.add(`chat-widget-window-visible-${instanceId}`);
                    elements.messages.scrollTop = elements.messages.scrollHeight;
                    if (isChatStarted && elements.input && !isWaitingForAgent) elements.input.focus();
                }, 10);
                elements.toggleButton.title = "Close Chat";
            }
        }

        async function endSession() {
            if (state.sessionMessages.length > 0 && state.customerInfo.name && state.customerInfo.email) {
                console.log(`[${instanceId}] Ending session and creating ticket...`);
                const conversation = state.sessionMessages.map(msg => `${msg.role === "user" ? "User" : state.agentInfo.name || "Agent"}: ${msg.text}`).join("\n");
                
                // Use the agent tied to the selected department
                const selectedAgent = state.agents.find(a => a.department === state.customerInfo.department);
                if (!selectedAgent) {
                    addMessage("Cannot create ticket: No agent available for this department.", "system");
                    console.error(`[${instanceId}] No agent found for department: ${state.customerInfo.department}`);
                    return;
                }

                const ticketRequest = {
                    user_id: config.userId,
                    agent_id: selectedAgent.id,
                    message: state.sessionMessages[0].text,
                    response: conversation,
                    platform: selectedAgent.helpdesk_platform || "zoho desk",
                    department_id: selectedAgent.helpdesk_department_id || null,
                    customer_email: state.customerInfo.email,
                    customer_name: state.customerInfo.name,
                    agent_name: state.agentInfo.name || "Unknown Agent"
                };
                console.log(`[${instanceId}] Ticket request body:`, ticketRequest);
                try {
                    const response = await fetch("http://localhost:8000/create_ticket", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(ticketRequest)
                    });
                    if (response.ok) {
                        const data = await response.json();
                        addMessage(`Session ended. Ticket created: ${data.ticket_id}`, "system");
                        console.log(`[${instanceId}] Ticket created: ${data.ticket_id}`);
                    } else {
                        const errorText = await response.text();
                        addMessage(`Failed to create ticket: ${errorText}`, "system");
                        console.error(`[${instanceId}] Ticket creation failed:`, errorText);
                    }
                } catch (error) {
                    addMessage(`Error creating ticket: ${error.message}`, "system");
                    console.error(`[${instanceId}] Ticket creation error:`, error);
                }
            }
            state.sessionMessages = [];
            persistedMessages.push(...state.sessionMessages);
            localStorage.setItem(chatHistoryKey, JSON.stringify(persistedMessages));
            resetInactivityTimer();
        }

        function clearChat() {
            if (confirm("Are you sure you want to end this session? This will create a ticket if messages exist.")) {
                console.log(`[${instanceId}] Clearing chat history...`);
                endSession().then(() => {
                    localStorage.removeItem(chatHistoryKey);
                    persistedMessages = [];
                    state.customerInfo = { name: null, email: null, language: "English", department: null };
                    isChatStarted = false;
                    if (elements.messages) elements.messages.innerHTML = '';
                    if (elements.chatContainer) elements.chatContainer.style.display = "none";
                    if (elements.prechatContainer) elements.prechatContainer.style.display = "flex";
                    isWaitingForAgent = false;
                });
            }
        }

        function resetInactivityTimer() {
            if (state.inactivityTimer) clearTimeout(state.inactivityTimer);
            if (isChatStarted && state.sessionMessages.length > 0) {
                state.inactivityTimer = setTimeout(() => {
                    addMessage("Session inactive for 5 minutes. Ending session...", "system");
                    endSession().then(() => {
                        if (elements.messages) elements.messages.innerHTML = '';
                        if (elements.chatContainer) elements.chatContainer.style.display = "none";
                        if (elements.prechatContainer) elements.prechatContainer.style.display = "flex";
                        state.customerInfo = { name: null, email: null, language: "English", department: null };
                        isChatStarted = false;
                    });
                }, INACTIVITY_TIMEOUT);
            }
        }

        // --- UI Element Creation ---

        function createActionButtons() {
            elements.actionsContainer = document.createElement("div");
            elements.actionsContainer.className = `chat-widget-actions-container-${instanceId}`;

            elements.toggleButton = document.createElement("button");
            elements.toggleButton.className = `chat-widget-action-button-${instanceId}`;
            elements.toggleButton.innerHTML = ICONS.chat;
            elements.toggleButton.title = "Open Chat";
            elements.toggleButton.onclick = toggleChatWindow;

            elements.actionsContainer.appendChild(elements.toggleButton);
            document.body.appendChild(elements.actionsContainer);
        }

        function createChatWindow() {
            elements.chatWindow = document.createElement("div");
            elements.chatWindow.className = `chat-widget-window-${instanceId}`;
            elements.header = createHeader();
            elements.messages = document.createElement("div");
            elements.messages.className = `chat-widget-messages-${instanceId}`;
            elements.footer = createFooter();
            elements.chatWindow.appendChild(elements.header);
            elements.chatWindow.appendChild(elements.messages);
            elements.chatWindow.appendChild(elements.footer);
            document.body.appendChild(elements.chatWindow);

            setTimeout(() => {
                elements.chatWindow.classList.add(`chat-widget-window-visible-${instanceId}`);
            }, 10);
        }

        function createFooter() {
            const footer = document.createElement("div");
            footer.className = `chat-widget-footer-${instanceId}`;

            elements.footerActions = document.createElement("div");
            elements.footerActions.className = `chat-widget-footer-actions-${instanceId}`;

            elements.clearButton = document.createElement("button");
            elements.clearButton.className = `chat-widget-footer-button-${instanceId}`;
            elements.clearButton.innerHTML = ICONS.clear;
            elements.clearButton.title = "End Session & Create Ticket";
            elements.clearButton.onclick = clearChat;

            elements.footerActions.appendChild(elements.clearButton);

            elements.prechatContainer = document.createElement("div");
            elements.prechatContainer.className = `chat-widget-prechat-container-${instanceId}`;
            const nameInput = document.createElement("input");
            nameInput.type = "text";
            nameInput.placeholder = "Your Name";
            nameInput.className = `chat-widget-prechat-input-${instanceId}`;
            const emailInput = document.createElement("input");
            emailInput.type = "email";
            emailInput.placeholder = "Your Email";
            emailInput.className = `chat-widget-prechat-input-${instanceId}`;
            const languageSelect = document.createElement("select");
            languageSelect.className = `chat-widget-prechat-select-${instanceId}`;
            LANGUAGE_OPTIONS.forEach(lang => {
                const option = document.createElement("option");
                option.value = lang;
                option.text = lang;
                languageSelect.appendChild(option);
            });
            const departmentSelect = document.createElement("select");
            departmentSelect.className = `chat-widget-prechat-select-${instanceId}`;
            departmentSelect.innerHTML = '<option value="">Select Department</option>';
            state.departments.forEach(dept => {
                const option = document.createElement("option");
                option.value = dept;
                option.text = dept;
                departmentSelect.appendChild(option);
            });
            const startButton = document.createElement("button");
            startButton.innerText = "Start Chat";
            startButton.className = `chat-widget-prechat-button-${instanceId}`;
            startButton.onclick = () => startChat(
                nameInput.value.trim(),
                emailInput.value.trim(),
                languageSelect.value,
                departmentSelect.value
            );
            elements.prechatContainer.appendChild(nameInput);
            elements.prechatContainer.appendChild(emailInput);
            elements.prechatContainer.appendChild(languageSelect);
            elements.prechatContainer.appendChild(departmentSelect);
            elements.prechatContainer.appendChild(startButton);

            elements.chatContainer = document.createElement("div");
            elements.chatContainer.className = `chat-widget-input-container-${instanceId}`;
            elements.input = document.createElement("input");
            elements.input.type = "text";
            elements.input.placeholder = "Type a message...";
            elements.input.className = `chat-widget-input-${instanceId}`;
            const inputActions = document.createElement("div");
            inputActions.className = `chat-widget-input-actions-${instanceId}`;
            const attachButton = document.createElement("button");
            attachButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>`;
            attachButton.className = `chat-widget-attach-button-${instanceId}`;
            attachButton.title = "Attach file (Not implemented)";
            elements.sendButton = document.createElement("button");
            elements.sendButton.innerHTML = ICONS.send;
            elements.sendButton.className = `chat-widget-send-button-${instanceId}`;
            elements.sendButton.title = "Send Message";
            inputActions.appendChild(attachButton);
            inputActions.appendChild(elements.sendButton);
            elements.chatContainer.appendChild(elements.input);
            elements.chatContainer.appendChild(inputActions);

            footer.appendChild(elements.footerActions);
            footer.appendChild(elements.prechatContainer);
            footer.appendChild(elements.chatContainer);

            elements.prechatContainer.style.display = isChatStarted ? "none" : "flex";
            elements.chatContainer.style.display = isChatStarted ? "flex" : "none";

            return footer;
        }

        function createHeader() {
            const header = document.createElement("div");
            header.className = `chat-widget-header-${instanceId}`;
            elements.agentAvatar = document.createElement("div");
            elements.agentAvatar.className = `chat-widget-agent-avatar-${instanceId}`;
            const avatarImg = document.createElement("img");
            avatarImg.className = `chat-widget-avatar-img-${instanceId}`;
            avatarImg.src = state.agentInfo.avatar || "https://via.placeholder.com/45";
            avatarImg.alt = "Agent";
            elements.agentAvatar.appendChild(avatarImg);
            const agentInfoDiv = document.createElement("div");
            agentInfoDiv.className = `chat-widget-agent-info-${instanceId}`;
            elements.agentName = document.createElement("div");
            elements.agentName.className = `chat-widget-agent-name-${instanceId}`;
            elements.agentName.innerText = state.agentInfo.name || "Support Agent";
            const statusIndicator = document.createElement("div");
            statusIndicator.className = `chat-widget-status-${instanceId}`;
            const onlineDot = document.createElement("div");
            onlineDot.className = `chat-widget-status-dot-${instanceId}`;
            const onlineText = document.createElement("span");
            onlineText.innerText = "Online";
            onlineText.className = `chat-widget-status-text-${instanceId}`;
            statusIndicator.appendChild(onlineDot);
            statusIndicator.appendChild(onlineText);
            agentInfoDiv.appendChild(elements.agentName);
            agentInfoDiv.appendChild(statusIndicator);
            elements.closeButton = document.createElement("button");
            elements.closeButton.innerText = "—";
            elements.closeButton.title = "Hide Chat";
            elements.closeButton.className = `chat-widget-close-button-${instanceId}`;
            elements.closeButton.onclick = toggleChatWindow;
            header.appendChild(elements.agentAvatar);
            header.appendChild(agentInfoDiv);
            header.appendChild(elements.closeButton);
            return header;
        }

        // --- Message Handling & Logic ---

        function loadMessages() {
            if (!config.userId || !config.apiKey) {
                addMessage("Error: Configuration missing.", "system");
                return;
            }
            if (elements.messages) elements.messages.innerHTML = '';

            if (isChatStarted && (persistedMessages.length > 0 || state.sessionMessages.length > 0)) {
                [...persistedMessages, ...state.sessionMessages].forEach(msg => {
                    const processedText = processMessageText(msg.text);
                    addMessage(processedText, msg.role, msg.avatarUrl, msg.agentName);
                    if (msg.role === "agent" && msg.agentName) {
                        state.agentInfo.name = msg.agentName;
                        state.agentInfo.avatar = msg.avatarUrl;
                        state.agentInfo.id = msg.agentId;
                        updateAgentInfo();
                    }
                });
                if (state.sessionMessages.length > 0 && state.sessionMessages[state.sessionMessages.length - 1].role === "user") {
                    isWaitingForAgent = true;
                    if (elements.input) elements.input.disabled = true;
                    if (elements.sendButton) elements.sendButton.disabled = true;
                }
            }
            setTimeout(() => {
                if (elements.messages) elements.messages.scrollTop = elements.messages.scrollHeight;
            }, 50);
        }

        async function loadDepartments() {
            const departmentSelect = elements.prechatContainer.querySelector(`.chat-widget-prechat-select-${instanceId}:nth-child(4)`);
            if (departmentSelect) {
                departmentSelect.innerHTML = '<option value="">Select Department</option>';
                state.departments.forEach(dept => {
                    const option = document.createElement("option");
                    option.value = dept;
                    option.text = dept;
                    departmentSelect.appendChild(option);
                });
                if (state.customerInfo.department) {
                    departmentSelect.value = state.customerInfo.department;
                }
            }
        }

        async function startChat(name, email, language, department) {
            if (!name || !email || !language || !department) {
                addMessage("Please provide all required fields: name, email, language, and department.", "system");
                return;
            }
            if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
                addMessage("Please enter a valid email address.", "system");
                return;
            }

            state.customerInfo.name = name;
            state.customerInfo.email = email;
            state.customerInfo.language = language;
            state.customerInfo.department = department;
            isChatStarted = true;

            // Update agentInfo based on selected department
            const selectedAgent = state.agents.find(a => a.department === department);
            if (selectedAgent) {
                state.agentInfo.name = selectedAgent.name;
                state.agentInfo.avatar = selectedAgent.avatar_url;
                state.agentInfo.id = selectedAgent.id;
            } else {
                console.error(`[${instanceId}] No agent found for selected department: ${department}`);
                addMessage("Error: No agent available for this department.", "system");
                return;
            }

            elements.prechatContainer.style.display = "none";
            elements.chatContainer.style.display = "flex";

            const greeting = "Hey there! How can I assist you today?";
            addMessage(greeting, "agent", state.agentInfo.avatar, state.agentInfo.name);
            state.sessionMessages.push({
                text: greeting,
                role: "agent",
                avatarUrl: state.agentInfo.avatar,
                agentName: state.agentInfo.name,
                agentId: state.agentInfo.id,
                customerName: name,
                customerEmail: email,
                language: language,
                department: department
            });

            if (elements.input) elements.input.focus();
            resetInactivityTimer();
            updateAgentInfo(); // Reflect the selected agent's info in the header
        }

        async function sendMessage() {
            if (isWaitingForAgent || !isChatStarted) return;

            const messageText = elements.input.value.trim();
            if (!messageText) return;
            console.log(`[${instanceId}] Sending message:`, messageText);
            const processedUserText = processMessageText(messageText);
            addMessage(processedUserText, "user");
            state.sessionMessages.push({ 
                text: messageText, 
                role: "user",
                customerName: state.customerInfo.name,
                customerEmail: state.customerInfo.email,
                language: state.customerInfo.language,
                department: state.customerInfo.department
            });
            elements.input.value = "";

            isWaitingForAgent = true;
            elements.input.disabled = true;
            elements.sendButton.disabled = true;

            let typingElement = null;
            try {
                typingElement = showTypingAnimation();
                const apiUrl = "http://localhost:8000/chat_widget";
                const history = state.sessionMessages.map(msg => ({
                    role: msg.role === "agent" ? "assistant" : msg.role,
                    content: msg.text
                })).slice(0, -1);
                const response = await fetch(apiUrl, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        user_id: config.userId,
                        message: messageText,
                        department: state.customerInfo.department,
                        api_key: config.apiKey,
                        history: history,
                        customer_email: state.customerInfo.email,
                        customer_name: state.customerInfo.name,
                        language: state.customerInfo.language
                    })
                });
                removeTypingAnimation(typingElement);
                if (!response.ok) {
                    const errorText = await response.text();
                    let errorDetail = errorText;
                    try { errorDetail = JSON.parse(errorText).detail || errorText; } catch(e) {}
                    throw new Error(`Server error ${response.status}: ${errorDetail}`);
                }
                const data = await response.json();
                if (!data.response) throw new Error("Received empty response.");
                if (data.agent && data.agent !== state.agentInfo.name) {
                    state.agentInfo.name = data.agent;
                    state.agentInfo.avatar = data.avatar_url || state.agentInfo.avatar;
                    updateAgentInfo();
                } else if (data.avatar_url && data.avatar_url !== state.agentInfo.avatar) {
                    state.agentInfo.avatar = data.avatar_url;
                    updateAgentInfo();
                }
                const processedAgentText = processMessageText(data.response);
                addMessage(processedAgentText, "agent", state.agentInfo.avatar, state.agentInfo.name);
                state.sessionMessages.push({ 
                    text: data.response,
                    role: "agent", 
                    avatarUrl: state.agentInfo.avatar, 
                    agentName: state.agentInfo.name,
                    agentId: state.agentInfo.id,
                    customerName: state.customerInfo.name,
                    customerEmail: state.customerInfo.email,
                    language: state.customerInfo.language,
                    department: state.customerInfo.department
                });

                isWaitingForAgent = false;
                elements.input.disabled = false;
                elements.sendButton.disabled = false;
                elements.input.focus();
                resetInactivityTimer();
            } catch (error) {
                console.error(`[${instanceId}] Fetch error:`, error);
                if (typingElement) removeTypingAnimation(typingElement);
                addMessage(`Error: ${error.message || 'Could not connect.'}`, "system");
                isWaitingForAgent = false;
                elements.input.disabled = false;
                elements.sendButton.disabled = false;
                elements.input.focus();
                resetInactivityTimer();
            }
        }

        function setupEventListeners() {
            if (elements.sendButton) {
                elements.sendButton.onclick = sendMessage;
            }

            if (elements.input) {
                elements.input.onkeypress = (e) => {
                    if (e.key === "Enter" && !e.shiftKey && !isWaitingForAgent) {
                        e.preventDefault();
                        sendMessage();
                    }
                };
            }

            let dragStartX, dragStartY, initialMouseX, initialMouseY;
            let isDragging = false;

            if (elements.header) {
                elements.header.addEventListener('mousedown', function(e) {
                    if (e.target === elements.closeButton) return;

                    isDragging = true;
                    initialMouseX = e.clientX;
                    initialMouseY = e.clientY;
                    const rect = elements.chatWindow.getBoundingClientRect();
                    dragStartX = rect.left;
                    dragStartY = rect.top;
                    if (window.getComputedStyle(elements.chatWindow).position !== 'fixed') {
                        elements.chatWindow.style.position = 'fixed';
                        elements.chatWindow.style.bottom = 'auto';
                        elements.chatWindow.style.right = 'auto';
                        elements.chatWindow.style.left = `${dragStartX}px`;
                        elements.chatWindow.style.top = `${dragStartY}px`;
                    }

                    document.documentElement.addEventListener('mousemove', drag);
                    document.documentElement.addEventListener('mouseup', stopDrag);
                    e.preventDefault();
                });
            }

            function drag(e) {
                if (!isDragging) return;
                const dx = e.clientX - initialMouseX;
                const dy = e.clientY - initialMouseY;
                const newLeft = dragStartX + dx;
                const newTop = dragStartY + dy;

                const chatWidth = elements.chatWindow.offsetWidth;
                const chatHeight = elements.chatWindow.offsetHeight;
                const viewportWidth = window.innerWidth;
                const viewportHeight = window.innerHeight;

                const constrainedLeft = Math.max(0, Math.min(newLeft, viewportWidth - chatWidth));
                const constrainedTop = Math.max(0, Math.min(newTop, viewportHeight - chatHeight));

                elements.chatWindow.style.left = `${constrainedLeft}px`;
                elements.chatWindow.style.top = `${constrainedTop}px`;
            }

            function stopDrag() {
                if (!isDragging) return;
                isDragging = false;
                document.documentElement.removeEventListener('mousemove', drag);
                document.documentElement.removeEventListener('mouseup', stopDrag);
            }
        }

        // --- Message Handling & Utility Functions ---

        function processMessageText(text) {
            if (typeof text !== 'string') return '';
            text = text.replace(/</g, "<").replace(/>/g, ">");
            text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            text = text.replace(/([^\\](?:\\\\)*)[*_](.*?)[*_]/g, '$1<em>$2</em>');
            text = text.replace(/\[([^\]]+)]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
            text = text.replace(/\n/g, '<br>');
            return text;
        }

        function showTypingAnimation() {
            if (!elements.messages) return null;
            const existing = elements.messages.querySelector(`.chat-widget-typing-indicator-${instanceId}`);
            if (existing) existing.remove();
            const indicator = document.createElement("div");
            indicator.className = `chat-widget-typing-indicator-${instanceId}`;
            const bubbles = document.createElement("div");
            bubbles.className = `chat-widget-typing-bubbles-${instanceId}`;
            for (let i = 0; i < 3; i++) {
                const dot = document.createElement("div");
                dot.className = `chat-widget-typing-dot-${instanceId}`;
                bubbles.appendChild(dot);
            }
            indicator.appendChild(bubbles);
            elements.messages.appendChild(indicator);
            elements.messages.scrollTop = elements.messages.scrollHeight;
            return indicator;
        }

        function removeTypingAnimation(element) {
            if (element && element.parentNode === elements.messages) {
                element.classList.add(`chat-widget-fade-out-${instanceId}`);
                setTimeout(() => {
                    if (element && element.parentNode === elements.messages) {
                        elements.messages.removeChild(element);
                    }
                }, 300);
            } else {
                const indicators = elements.messages?.querySelectorAll(`.chat-widget-typing-indicator-${instanceId}`);
                indicators?.forEach(ind => ind.remove());
            }
        }

        function updateAgentInfo() {
            if (!elements.agentName || !elements.agentAvatar) return;
            if (state.agentInfo.name) elements.agentName.innerText = state.agentInfo.name;
            const img = elements.agentAvatar.querySelector(`.chat-widget-avatar-img-${instanceId}`);
            if (img) img.src = state.agentInfo.avatar || "https://via.placeholder.com/45";
        }

        function addMessage(text, role = "system", avatarUrl = null, senderName = null) {
            if (!elements.messages) {
                console.error(`[${instanceId}] Cannot add message, container missing`);
                return;
            }
            const msgDiv = document.createElement("div");
            msgDiv.classList.add(`chat-widget-message-${instanceId}`, `chat-widget-message-${role}-${instanceId}`);
            const contentDiv = document.createElement("div");
            contentDiv.className = `chat-widget-message-content-${instanceId}`;
            contentDiv.innerHTML = text;
            if (role === 'agent' && senderName && senderName !== elements.agentName?.innerText) {
                const nameDiv = document.createElement("div");
                nameDiv.className = `chat-widget-message-name-${instanceId}`;
                nameDiv.innerText = senderName;
                msgDiv.appendChild(nameDiv);
            }
            msgDiv.appendChild(contentDiv);
            msgDiv.classList.add(`chat-widget-message-incoming-${instanceId}`);
            elements.messages.appendChild(msgDiv);
            setTimeout(() => {
                elements.messages.scrollTop = elements.messages.scrollHeight;
            }, 0);
        }

        // --- Initialization ---
        fetchAgentDetails().then(() => {
            createActionButtons();
            updateAgentInfo();
        });
    }
})();