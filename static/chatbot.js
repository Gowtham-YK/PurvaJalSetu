/* =========================================================
   JALSETU WASTEWATER ASSISTANT
   Professional Chatbot Frontend
========================================================= */

document.addEventListener("DOMContentLoaded", function () {

    /* =====================================================
       GET USER LOCATION
    ===================================================== */

    window.chatbotLatitude = null;
    window.chatbotLongitude = null;

    if (navigator.geolocation) {

        navigator.geolocation.getCurrentPosition(

            function (position) {

                window.chatbotLatitude =
                    position.coords.latitude;

                window.chatbotLongitude =
                    position.coords.longitude;

                console.log(
                    "Chatbot location:",
                    window.chatbotLatitude,
                    window.chatbotLongitude
                );

            },

            function () {

                console.log(
                    "Location permission not granted."
                );

            },

            {
                enableHighAccuracy: true,
                timeout: 10000,
                maximumAge: 300000
            }
        );
    }


    /* =====================================================
       GET CHATBOT ELEMENTS
    ===================================================== */

    const toggle =
        document.getElementById("ww-chatbot-toggle");

    const windowElement =
        document.getElementById("ww-chatbot-window");

    const close =
        document.getElementById("ww-chatbot-close");

    const input =
        document.getElementById("ww-chatbot-input");

    const send =
        document.getElementById("ww-chatbot-send");

    const messages =
        document.getElementById("ww-chatbot-messages");

    const typing =
        document.getElementById("ww-chatbot-typing");


    /* =====================================================
       SAFETY CHECK
    ===================================================== */

    if (
        !toggle ||
        !windowElement ||
        !close ||
        !input ||
        !send ||
        !messages ||
        !typing
    ) {

        console.error(
            "Wastewater chatbot: required element missing."
        );

        return;
    }


    /* =====================================================
       STATE
    ===================================================== */

    let isSending = false;


    /* =====================================================
       SCROLL TO BOTTOM
    ===================================================== */

    function scrollToBottom() {

        requestAnimationFrame(function () {

            messages.scrollTo({
                top: messages.scrollHeight,
                behavior: "smooth"
            });

        });
    }


    /* =====================================================
       OPEN CHAT
    ===================================================== */

    function openChat() {

        windowElement.classList.remove(
            "ww-chatbot-hidden"
        );

        toggle.setAttribute(
            "aria-expanded",
            "true"
        );

        scrollToBottom();

        setTimeout(function () {
            input.focus();
        }, 100);
    }


    /* =====================================================
       CLOSE CHAT
    ===================================================== */

    function closeChat() {

        windowElement.classList.add(
            "ww-chatbot-hidden"
        );

        toggle.setAttribute(
            "aria-expanded",
            "false"
        );

        toggle.focus();
    }


    /* =====================================================
       OPEN / CLOSE EVENTS
    ===================================================== */

    toggle.addEventListener(
        "click",
        openChat
    );

    close.addEventListener(
        "click",
        closeChat
    );


    /* =====================================================
       ESCAPE KEY
    ===================================================== */

    document.addEventListener(
        "keydown",
        function (event) {

            if (
                event.key === "Escape" &&
                !windowElement.classList.contains(
                    "ww-chatbot-hidden"
                )
            ) {

                closeChat();
            }

        }
    );


    /* =====================================================
       ADD MESSAGE
    ===================================================== */

    function addMessage(message, sender) {

        const wrapper =
            document.createElement("div");

        wrapper.classList.add(
            "ww-chat-message"
        );

        if (sender === "user") {

            wrapper.classList.add(
                "ww-chat-user"
            );

        } else {

            wrapper.classList.add(
                "ww-chat-bot"
            );
        }


        const bubble =
            document.createElement("div");

        bubble.classList.add(
            "ww-chat-bubble"
        );

        bubble.textContent =
            String(message);


        wrapper.appendChild(bubble);

        messages.appendChild(wrapper);

        scrollToBottom();

        return wrapper;
    }


    /* =====================================================
       QUICK ACTIONS
    ===================================================== */

    function addQuickActions() {

        const existing =
            document.querySelector(
                ".ww-chatbot-quick-actions"
            );

        if (existing) {
            return;
        }


        const container =
            document.createElement("div");

        container.classList.add(
            "ww-chatbot-quick-actions"
        );


        const actions = [
            {
                icon: "🏭",
                title: "Find an STP",
                message: "Find the nearest STP"
            },
            {
                icon: "📦",
                title: "My Orders",
                message: "Show me my latest order"
            },
            {
                icon: "🚛",
                title: "Track Delivery",
                message: "What is my delivery status?"
            },
            {
                icon: "💧",
                title: "STP Capacity",
                message: "Do you have any plants with enough capacity for 50 KLD?"
            }
        ];


        actions.forEach(function (action) {

            const button =
                document.createElement("button");

            button.type = "button";

            button.classList.add(
                "ww-chatbot-quick-action"
            );


            button.innerHTML = `
                <span class="ww-chatbot-quick-icon">
                    ${action.icon}
                </span>
                <span class="ww-chatbot-quick-title">
                    ${action.title}
                </span>
            `;


            button.addEventListener(
                "click",
                function () {

                    input.value =
                        action.message;

                    sendMessage();

                }
            );


            container.appendChild(button);

        });


        messages.appendChild(container);

        scrollToBottom();
    }


    /* =====================================================
       SHOW INITIAL QUICK ACTIONS
    ===================================================== */

    addQuickActions();


    /* =====================================================
       TYPING INDICATOR
    ===================================================== */

    function showTyping() {

        typing.classList.remove(
            "ww-chatbot-typing-hidden"
        );

        typing.innerHTML = `
            <span class="ww-chatbot-typing-label">
            Juno is typing            
            </span>
            <span class="ww-chatbot-typing-dots">
                <span></span>
                <span></span>
                <span></span>
            </span>
        `;

        scrollToBottom();
    }


    /* =====================================================
       HIDE TYPING INDICATOR
    ===================================================== */

    function hideTyping() {

        typing.classList.add(
            "ww-chatbot-typing-hidden"
        );

        typing.innerHTML = "";
    }


    /* =====================================================
       SET SENDING STATE
    ===================================================== */

    function setSendingState(sending) {

        isSending = sending;

        input.disabled = sending;

        send.disabled = sending;


        if (sending) {

            send.innerHTML = `
                <span class="ww-chatbot-send-spinner"></span>
            `;

        } else {

            send.innerHTML = "➤";
        }
    }


    /* =====================================================
       SEND MESSAGE
    ===================================================== */

    async function sendMessage() {

        const message =
            input.value.trim();


        if (!message || isSending) {
            return;
        }


        /* Remove quick actions after first interaction */

        const quickActions =
            document.querySelector(
                ".ww-chatbot-quick-actions"
            );

        if (quickActions) {
            quickActions.remove();
        }


        /* Show user message */

        addMessage(
            message,
            "user"
        );


        /* Clear input */

        input.value = "";


        /* Set loading state */

        setSendingState(true);

        showTyping();


        try {

            const response =
                await fetch(
                    "/api/chat",
                    {
                        method: "POST",

                        headers: {
                            "Content-Type":
                                "application/json"
                        },

                        body: JSON.stringify({

                            message: message,

                            latitude:
                                window.chatbotLatitude ||
                                null,

                            longitude:
                                window.chatbotLongitude ||
                                null
                        })
                    }
                );


            let data = null;


            try {

                data =
                    await response.json();

            } catch (jsonError) {

                console.error(
                    "Chatbot response was not valid JSON:",
                    jsonError
                );

            }


            if (
                response.ok &&
                data &&
                data.reply
            ) {

                addMessage(
                    data.reply,
                    "bot"
                );

            } else {

                addMessage(
                    "Sorry, I couldn't process that request right now. Please try again.",
                    "bot"
                );

                console.error(
                    "Chatbot API error:",
                    response.status,
                    data
                );
            }


        } catch (error) {

            console.error(
                "Chatbot connection error:",
                error
            );


            addMessage(
                "Sorry, I'm unable to connect to Juno right now. Please check your connection and try again.",
                "bot"
            );


        } finally {

            hideTyping();

            setSendingState(false);

            input.focus();
        }
    }


    /* =====================================================
       SEND BUTTON
    ===================================================== */

    send.addEventListener(
        "click",
        sendMessage
    );


    /* =====================================================
       ENTER KEY
    ===================================================== */

    input.addEventListener(
        "keydown",
        function (event) {

            if (
                event.key === "Enter" &&
                !event.shiftKey
            ) {

                event.preventDefault();

                sendMessage();
            }

        }
    );


    /* =====================================================
       INPUT STATE
    ===================================================== */

    input.addEventListener(
        "input",
        function () {

            send.disabled =
                isSending ||
                input.value.trim().length === 0;

        }
    );


    /* =====================================================
       INITIAL SEND STATE
    ===================================================== */

    send.disabled = true;


    /* =====================================================
       INITIAL ARIA STATE
    ===================================================== */

    toggle.setAttribute(
        "aria-expanded",
        "false"
    );

});