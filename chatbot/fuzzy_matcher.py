from rapidfuzz import fuzz


# =========================================================
# INTENT EXAMPLES
# =========================================================

INTENT_EXAMPLES = {

    # -----------------------------------------------------
    # GREETING
    # -----------------------------------------------------
    "greeting": [
        "hello",
        "hi",
        "hey",
        "hai",
        "good morning",
        "good afternoon",
        "good evening",
        "hello there",
        "hey there",
    ],

    # -----------------------------------------------------
    # HELP
    # -----------------------------------------------------
    "help": [
        "what can you do",
        "what can you help me with",
        "what do you do",
        "help",
        "help me",
        "what are your features",
        "what are your capabilities",
        "what all can you do",
        "what stuff can you help me with",
        "what can you actually help me with",
        "what all stuff can you help me with",
        "what can you actually do",
    ],

    # -----------------------------------------------------
    # USER ROLE
    # -----------------------------------------------------
    "user_role": [
        "what is my role",
        "who am i",
        "what account am i using",
        "what is my account",
        "which account am i using",
        "what role am i logged in as",
        "what is my user role",
    ],

    # -----------------------------------------------------
    # STP INFORMATION
    # -----------------------------------------------------
    "stp_information": [
        "how many stps are there",
        "how many stps",
        "show available stps",
        "list the stps",
        "show all stps",
        "what stps are available",
        "which stps are available",
        "what is the stp availability",
        "show me all treatment plants",
        "how many treatment plants are available",
        "show treatment plants",
        "list treatment plants",
        "how many plants are there",
    ],

    # -----------------------------------------------------
    # NEAREST STP
    # -----------------------------------------------------
    "nearest_stp": [
        "what is the nearest stp",
        "what's the nearest stp",
        "which stp is closest to me",
        "which stp is nearest to me",
        "find the nearest stp",
        "find the closest stp",
        "where is the nearest stp",
        "where is the closest stp",
        "what stp is near me",
        "which treatment plant is closest",
        "which treatment plant is nearest",
        "find a treatment plant near me",
        "which plant is closest to where i am",
        "find a treatment plant near my location",
        "find a treatment plant close to me",
        "what plant is near me",
        "find a plant close to me",
    ],

    # -----------------------------------------------------
    # STP RECOMMENDATION
    # -----------------------------------------------------
    "stp_recommendation": [
        "which stp should i choose",
        "which stp should i select",
        "which stp is best",
        "recommend an stp",
        "recommend a stp",
        "find a suitable stp",
        "which stp is suitable",
        "which stp is suitable for my requirement",
        "which stp can provide the water i need",
        "which treatment plant can supply my requirement",
        "i need water which stp should i choose",
        "recommend a treatment plant",
        "find the best treatment plant",
    ],

    # -----------------------------------------------------
    # ORDER STATUS
    # -----------------------------------------------------
    "order_status": [
        "what is my order status",
        "what's my order status",
        "where is my order",
        "where's my order",
        "what is happening with my order",
        "what's happening with my order",
        "what is going on with my order",
        "what is going on with my water request",
        "can you tell me whats going on with my water request",
        "can you tell me whats happening with my water request",
        "what happened to my water request",
        "what happened to my order",
        "has my order been approved",
        "has my request been approved",
        "did the stp approve my order",
        "did the stp approve my request",
        "did the treatment plant approve my request",
        "did they approve my order",
        "can you check my order",
        "can you check my order status",
        "check my order status",
        "i need an update on my order",
        "can you check what happened to my request",
        "whats going on with my order",
        "is my order approved yet",
        "is my water request approved yet",
    ],

    # -----------------------------------------------------
    # ORDER HISTORY
    # -----------------------------------------------------
    "order_history": [
        "show my order history",
        "what is my order history",
        "show my orders",
        "list my orders",
        "show all my orders",
        "show all my previous orders",
        "show my previous orders",
        "what orders have i placed",
        "what orders did i place",
        "show all my water requests",
        "show all my previous water requests",
        "can you show me all my previous water requests",
        "can you pull up all the requests i've made before",
        "show my past requests",
        "show my old orders",
        "what have i ordered before",
        "show everything i ordered",
        "all my requests",
    ],

    # -----------------------------------------------------
    # LATEST ORDER
    # -----------------------------------------------------
    "latest_order": [
        "show me my latest order",
        "what is my latest order",
        "show my latest order",
        "what was my latest order",
        "what was my last order",
        "show me the last order i placed",
        "what did i order last",
        "show my recent order",
        "what was my most recent order",
        "show my most recent order",
        "what was my latest water request",
        "show my latest water request",
        "what was my last request",
        "show my last request",
    ],

    # -----------------------------------------------------
    # ORDER QUANTITY
    # -----------------------------------------------------
    "order_quantity": [
        "how much water did i order",
        "how much did i order",
        "what quantity did i order",
        "how many kld did i order",
        "what is my order quantity",
        "how much water was in my order",
        "what quantity of water did i request",
        "how much water did i request",
        "what was the quantity of my order",
    ],

    # -----------------------------------------------------
    # TOTAL ORDER QUANTITY
    # -----------------------------------------------------
    "total_order_quantity": [
        "how much water have i ordered",
        "how much have i ordered altogether",
        "what is my total water quantity",
        "what is my total ordered quantity",
        "how many kld have i ordered in total",
        "what is my total order quantity",
        "how much water did i order in total",
        "what is the total amount of water i have ordered",
        "how much water have i requested altogether",
        "how much water have i requested in total",
        "what have i requested overall",
    ],

    # -----------------------------------------------------
    # ORDER COUNT
    # -----------------------------------------------------
    "order_count": [
        "how many orders have i placed",
        "how many orders did i make",
        "how many orders have i made",
        "how many orders do i have",
        "how many orders are there",
        "how many orders have i placed so far",
        "how many orders have i made so far",
        "how many water requests have i made",
        "how many requests have i placed",
        "number of orders i placed",
    ],

    # -----------------------------------------------------
    # TANKER STATUS
    # -----------------------------------------------------
    "tanker_status": [
        "where is my tanker",
        "where's my tanker",
        "what is my tanker status",
        "what's my tanker status",
        "has my tanker been assigned",
        "has my tanker been assigned yet",
        "is my tanker assigned",
        "did they assign my tanker",
        "did they assign a tanker for my order",
        "has a tanker been assigned",
        "has a tanker been assigned to my order",
        "has someone assigned a tanker to me",
        "do i have a tanker assigned",
        "is there a tanker assigned for me",
        "do i have a tanker yet",
        "has anyone assigned a tanker to my order yet",
    ],

    # -----------------------------------------------------
    # DELIVERY STATUS
    # -----------------------------------------------------
    "delivery_status": [
        "what is my delivery status",
        "what's my delivery status",
        "where is my delivery",
        "where's my delivery",
        "when will my delivery arrive",
        "when will my order arrive",
        "when will my water arrive",
        "when am i getting my water",
        "when will i get my water",
        "when am i going to get my water",
        "when is my water arriving",
        "when can i expect my water",
        "when will i receive my water",
        "when am i going to receive my water",
        "has my order been delivered",
        "is my order out for delivery",
        "when should i expect my water",
        "how soon will the water reach me",
        "when is my water delivery coming",
    ],
}


# =========================================================
# INTENT SIGNALS
# =========================================================

INTENT_SIGNALS = {

    "greeting": [
        "hello",
        "hi",
        "hey",
        "hai",
        "morning",
        "afternoon",
        "evening",
    ],

    "help": [
        "help",
        "features",
        "capabilities",
        "assist",
        "support",
    ],

    "user_role": [
        "role",
        "account",
        "profile",
    ],

    "stp_information": [
        "stp",
        "stps",
        "available",
        "availability",
        "capacity",
        "treatment plant",
    ],

    "nearest_stp": [
        "nearest",
        "closest",
        "nearby",
        "near me",
        "treatment plant",
        "plant",
    ],

    "stp_recommendation": [
        "recommend",
        "suitable",
        "best",
        "choose",
        "select",
    ],

    "order_status": [
        "order",
        "status",
        "approved",
        "approve",
        "request",
        "happening",
        "going on",
        "update",
    ],

    "order_history": [
        "orders",
        "history",
        "past",
        "previous",
        "old",
        "placed",
        "before",
        "requests",
    ],

    "latest_order": [
        "latest",
        "last",
        "recent",
        "most recent",
    ],

    "order_quantity": [
        "quantity",
        "water",
        "kld",
        "requested",
    ],

    "total_order_quantity": [
        "total",
        "altogether",
        "overall",
        "combined",
        "in total",
    ],

    "order_count": [
        "how many",
        "count",
        "number",
        "requests",
    ],

    "tanker_status": [
        "tanker",
        "assigned",
        "assignment",
    ],

    "delivery_status": [
        "delivery",
        "delivered",
        "arrive",
        "arriving",
        "reach",
        "receive",
    ],
}


# =========================================================
# TEXT NORMALIZATION
# =========================================================

def normalize_text(text):
    """
    Normalize user input before matching.
    """

    return " ".join(
        str(text)
        .lower()
        .strip()
        .split()
    )


# =========================================================
# FUZZY INTENT MATCHER
# =========================================================

def find_fuzzy_intent(
    text,
    threshold=72,
    ambiguity_margin=7
):
    """
    Hybrid fuzzy intent matcher.

    Returns:
        (intent, score)

    or:
        (None, score)
    """

    text = normalize_text(text)

    if not text:
        return None, 0

    # =====================================================
    # HIGH PRIORITY INTENT RULES
    # =====================================================

    # -----------------------------------------------------
    # ORDER COUNT
    # -----------------------------------------------------
    if (
        "how many" in text
        and any(
            word in text
            for word in (
                "order",
                "orders",
                "request",
                "requests",
            )
        )
    ):
        return "order_count", 100

    # -----------------------------------------------------
    # TOTAL ORDER QUANTITY
    # -----------------------------------------------------
    if (
        any(
            phrase in text
            for phrase in (
                "altogether",
                "in total",
                "overall",
                "combined",
                "total quantity",
                "total water",
                "total kld",
            )
        )
        and any(
            word in text
            for word in (
                "water",
                "quantity",
                "kld",
                "order",
                "orders",
                "request",
                "requests",
            )
        )
    ):
        return "total_order_quantity", 100

    # -----------------------------------------------------
    # LATEST ORDER QUANTITY
    # -----------------------------------------------------
    latest_words = (
        "latest",
        "last",
        "recent",
        "most recent",
    )

    if (
        any(
            word in text
            for word in latest_words
        )
        and any(
            word in text
            for word in (
                "order",
                "request",
            )
        )
        and any(
            phrase in text
            for phrase in (
                "how much",
                "quantity",
                "water",
                "kld",
            )
        )
    ):
        return "latest_order_quantity", 100

    # =====================================================
    # STRONG PATTERN RULES
    # =====================================================

    strong_patterns = {

        # -------------------------------------------------
        # HELP
        # -------------------------------------------------
        "help": [
            ("what can", "help"),
            ("can you", "help"),
            ("features", "have"),
            ("capabilities", "have"),
        ],

        # -------------------------------------------------
        # USER ROLE
        # -------------------------------------------------
        "user_role": [
            ("my", "role"),
            ("my", "account"),
            ("who", "i"),
        ],

        # -------------------------------------------------
        # STP INFORMATION
        # -------------------------------------------------
        "stp_information": [
            ("how many", "stps"),
            ("show", "stps"),
            ("available", "stps"),
            ("list", "stps"),
            ("treatment plants", "available"),
        ],

        # -------------------------------------------------
        # ORDER COUNT
        # -------------------------------------------------
        "order_count": [
            ("number of", "orders"),
            ("number of", "requests"),
            ("count", "orders"),
            ("count", "requests"),
            ("orders", "so far"),
            ("requests", "so far"),
        ],

        # -------------------------------------------------
        # TOTAL ORDER QUANTITY
        # -------------------------------------------------
        "total_order_quantity": [
            ("total", "water"),
            ("total", "quantity"),
            ("total", "kld"),
            ("water", "in total"),
            ("ordered", "in total"),
            ("requested", "in total"),
            ("requested", "altogether"),
            ("water", "altogether"),
        ],

        # -------------------------------------------------
        # LATEST ORDER QUANTITY
        # -------------------------------------------------
        "latest_order_quantity": [
            ("latest", "quantity"),
            ("latest", "water"),
            ("latest", "kld"),
            ("last", "quantity"),
            ("last", "water"),
            ("last", "kld"),
            ("recent", "quantity"),
            ("recent", "water"),
            ("recent", "kld"),
        ],

        # -------------------------------------------------
        # ORDER QUANTITY
        # -------------------------------------------------
        "order_quantity": [
            ("how much", "water"),
            ("how much", "did i order"),
            ("how much", "did i request"),
            ("quantity", "order"),
            ("quantity", "water"),
            ("how many", "kld"),
        ],

        # -------------------------------------------------
        # ORDER HISTORY
        # -------------------------------------------------
        "order_history": [
            ("order", "history"),
            ("past", "orders"),
            ("previous", "orders"),
            ("old", "orders"),
            ("all", "orders"),
            ("show", "orders"),
            ("previous", "requests"),
            ("past", "requests"),
            ("all", "requests"),
            ("show", "requests"),
            ("water", "requests"),
            ("everything", "ordered"),
        ],

        # -------------------------------------------------
        # LATEST ORDER
        # -------------------------------------------------
        "latest_order": [
            ("latest", "order"),
            ("last", "order"),
            ("recent", "order"),
            ("most recent", "order"),
            ("latest", "request"),
            ("last", "request"),
            ("recent", "request"),
            ("most recent", "request"),
        ],

        # -------------------------------------------------
        # ORDER STATUS
        # -------------------------------------------------
        "order_status": [
            ("order", "status"),
            ("order", "approved"),
            ("request", "approved"),
            ("order", "happening"),
            ("order", "going on"),
            ("status", "request"),
            ("update", "order"),
            ("update", "request"),
            ("request", "status"),
            ("where", "order"),
        ],

        # -------------------------------------------------
        # TANKER STATUS
        # -------------------------------------------------
        "tanker_status": [
            ("tanker", "assigned"),
            ("tanker", "assignment"),
            ("where", "tanker"),
            ("status", "tanker"),
            ("tanker", "yet"),
        ],

        # -------------------------------------------------
        # DELIVERY STATUS
        # -------------------------------------------------
        "delivery_status": [
            ("delivery", "status"),
            ("water", "arrive"),
            ("water", "arriving"),
            ("water", "reach"),
            ("water", "receive"),
            ("order", "arrive"),
            ("order", "delivered"),
            ("how soon", "water"),
        ],

        # -------------------------------------------------
        # NEAREST STP
        # -------------------------------------------------
        "nearest_stp": [
            ("nearest", "stp"),
            ("closest", "stp"),
            ("nearest", "treatment plant"),
            ("closest", "treatment plant"),
            ("near", "treatment plant"),
            ("near me", "stp"),
            ("nearby", "stp"),
            ("plant", "closest"),
            ("plant", "nearest"),
            ("close to", "me"),
        ],

        # -------------------------------------------------
        # STP RECOMMENDATION
        # -------------------------------------------------
        "stp_recommendation": [
            ("recommend", "stp"),
            ("recommend", "treatment plant"),
            ("suitable", "stp"),
            ("best", "stp"),
            ("choose", "stp"),
            ("select", "stp"),
        ],
    }

    # =====================================================
    # STRONG PATTERN MATCHING
    # =====================================================

    for intent, patterns in strong_patterns.items():

        for first, second in patterns:

            if first in text and second in text:

                # Prevent latest-order quantity questions
                # from being incorrectly classified as
                # general order quantity.
                if (
                    intent == "order_quantity"
                    and any(
                        word in text
                        for word in latest_words
                    )
                ):
                    continue

                return intent, 100

    # =====================================================
    # GENERIC QUESTIONS
    # =====================================================

    generic_questions = [
        "tell me about water",
        "tell me about the water",
        "what about water",
        "i want water",
        "i need water",
    ]

    if text in generic_questions:
        return None, 0

    # =====================================================
    # FUZZY MATCHING
    # =====================================================

    best_intent = None
    best_score = 0
    second_best_score = 0

    for intent, examples in INTENT_EXAMPLES.items():

        intent_best_score = 0

        for example in examples:

            example = normalize_text(example)

            ratio = fuzz.ratio(
                text,
                example
            )

            token_sort = fuzz.token_sort_ratio(
                text,
                example
            )

            token_set = fuzz.token_set_ratio(
                text,
                example
            )

            fuzzy_score = max(
                ratio,
                (
                    token_sort * 0.6
                    + token_set * 0.4
                )
            )

            # -------------------------------------------------
            # SIGNAL BONUS
            # -------------------------------------------------

            signal_matches = 0

            for signal in INTENT_SIGNALS.get(
                intent,
                []
            ):

                if signal in text:
                    signal_matches += 1

            signal_bonus = min(
                signal_matches * 2,
                6
            )

            final_score = min(
                fuzzy_score + signal_bonus,
                100
            )

            if final_score > intent_best_score:
                intent_best_score = final_score

        # -------------------------------------------------
        # KEEP TOP TWO INTENTS
        # -------------------------------------------------

        if intent_best_score > best_score:

            second_best_score = best_score
            best_score = intent_best_score
            best_intent = intent

        elif intent_best_score > second_best_score:

            second_best_score = intent_best_score

    # =====================================================
    # CONFIDENCE CHECK
    # =====================================================

    if best_score < threshold:
        return None, round(best_score, 2)

    # =====================================================
    # AMBIGUITY CHECK
    # =====================================================

    if (
        second_best_score > 0
        and best_score < 88
        and (
            best_score - second_best_score
            < ambiguity_margin
        )
    ):
        return None, round(best_score, 2)

    # =====================================================
    # FINAL RESULT
    # =====================================================

    return best_intent, round(best_score, 2)