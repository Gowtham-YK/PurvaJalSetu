/* =========================================================
   WASTEWATER CHATBOT
========================================================= */

document.addEventListener("DOMContentLoaded", function () {

        // =====================================================
    // GET USER LOCATION
    // =====================================================

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

            function (error) {

                console.log(
                    "Location permission not granted."
                );

            }

        );

    }

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
       OPEN CHAT
    ===================================================== */

    toggle.addEventListener("click", function () {

        windowElement.classList.remove(
            "ww-chatbot-hidden"
        );

        input.focus();

    });


    /* =====================================================
       CLOSE CHAT
    ===================================================== */

    close.addEventListener("click", function () {

        windowElement.classList.add(
            "ww-chatbot-hidden"
        );

    });


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

        bubble.textContent = message;


        wrapper.appendChild(bubble);

        messages.appendChild(wrapper);


        messages.scrollTop =
            messages.scrollHeight;

    }


    /* =====================================================
       SEND MESSAGE
    ===================================================== */

    async function sendMessage() {

        const message =
            input.value.trim();


        if (!message) {
            return;
        }


        /* Show user's message */

        addMessage(
            message,
            "user"
        );


        /* Clear input */

        input.value = "";


        /* Show typing */

        typing.classList.remove(
            "ww-chatbot-typing-hidden"
        );


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
                        latitude: window.chatbotLatitude || null,
                        longitude: window.chatbotLongitude || null
                    })
                    }
                );


            const data =
                await response.json();


            if (data.reply) {

                addMessage(
                    data.reply,
                    "bot"
                );

            } else {

                addMessage(
                    "I couldn't understand that request.",
                    "bot"
                );

            }


        } catch (error) {

            console.error(
                "Chatbot error:",
                error
            );


            addMessage(
                "Sorry, I'm unable to connect to the assistant right now.",
                "bot"
            );

        } finally {

            typing.classList.add(
                "ww-chatbot-typing-hidden"
            );

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

            if (event.key === "Enter") {

                event.preventDefault();

                sendMessage();

            }

        }
    );

});