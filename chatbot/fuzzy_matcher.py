from rapidfuzz import fuzz


# =========================================================
# INTENT EXAMPLES
# =========================================================

INTENT_EXAMPLES = {

    "greeting": [
        "hello",
        "hi",
        "hey",
        "hai",
        "good morning",
        "good afternoon",
        "good evening",
    ],

    "help": [
        "what can you do",
        "what can you help me with",
        "what do you do",
        "help",
        "help me",
        "what are your features",
        "what are your capabilities",
    ],

    "user_role": [
        "what is my role",
        "who am i",
        "what account am i using",
        "what is my account",
        "which account am i using",
    ],

    "stp_information": [
        "how many stps are there",
        "how many stps",
        "show available stps",
        "list the stps",
        "show all stps",
        "what stps are available",
        "which stps are available",
        "what is the stp availability",
    ],

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
    ],

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
    ],

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
    ],

    "order_history": [
        "show my order history",
        "what is my order history",
        "show my orders",
        "list my orders",
        "what orders have i placed",
        "what orders did i place",
        "show my previous orders",
        "show all my orders",
        "what orders have i made",
        "show me all the orders i have made",
        "tell me all the orders i have placed",
        "can you show all my orders",
        "i want to see my past orders",
        "show me my past orders",
        "i want to see my previous orders",
        "can i see my previous orders",
        "show me the orders i made",
        "what orders have i made in the past",
        "i want to see everything i ordered before",
        "can i see my old orders",
    ],

    "latest_order": [
        "what was my previous order",
        "what was my last order",
        "what did i order last",
        "show my latest order",
        "what is my latest order",
        "what was my latest order",
        "tell me about my last order",
        "tell me about my previous order",
        "show my most recent order",
        "what is my most recent order",
    ],

    "order_quantity": [
        "how much water did i order",
        "how much did i order",
        "what quantity did i order",
        "how many kld did i order",
        "what is my order quantity",
        "how much water was in my order",
        "what quantity of water did i request",
        "how much water did i request",
    ],

    "total_order_quantity": [
        "how much water have i ordered",
        "how much have i ordered",
        "what is my total water quantity",
        "what is my total ordered quantity",
        "how many kld have i ordered in total",
        "what is my total order quantity",
        "how much water did i order in total",
        "what is the total amount of water i have ordered",
    ],

    "order_count": [
        "how many orders have i placed",
        "how many orders did i make",
        "how many orders have i made",
        "how many orders do i have",
        "how many orders are there",
        "how many orders have i placed so far",
        "how many orders have i made so far",
    ],

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
    ],

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
    ],

    "latest_order": [
        "order",
        "latest",
        "last",
        "recent",
        "previous",
    ],

    "order_quantity": [
        "order",
        "quantity",
        "water",
        "kld",
        "requested",
    ],

    "total_order_quantity": [
        "orders",
        "total",
        "quantity",
        "water",
        "kld",
    ],

    "order_count": [
        "orders",
        "how many",
        "count",
        "number",
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
        "water",
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
    # STRONG PATTERN RULES
    # =====================================================

    strong_patterns = {

        "order_count": [
            ("how many", "orders"),
            ("number of", "orders"),
            ("count", "orders"),
            ("orders", "so far"),
        ],

        "total_order_quantity": [
            ("total", "water"),
            ("total", "quantity"),
            ("total", "kld"),
            ("water", "in total"),
            ("ordered", "in total"),
        ],

        "order_quantity": [
            ("how much", "water"),
            ("how much", "did i order"),
            ("how much", "did i request"),
            ("quantity", "order"),
            ("quantity", "water"),
            ("how many", "kld"),
        ],

        "order_history": [
            ("order", "history"),
            ("past", "orders"),
            ("previous", "orders"),
            ("old", "orders"),
            ("orders", "placed"),
            ("orders", "made"),
            ("everything", "ordered"),
        ],

        "latest_order": [
            ("latest", "order"),
            ("last", "order"),
            ("recent", "order"),
        ],

        "order_status": [
            ("order", "status"),
            ("order", "approved"),
            ("request", "approved"),
            ("order", "happening"),
            ("order", "going on"),
            ("water", "request"),
            ("update", "order"),
            ("update", "request"),
        ],

        "tanker_status": [
            ("tanker", "assigned"),
            ("tanker", "assignment"),
            ("where", "tanker"),
            ("status", "tanker"),
            ("tanker", "yet"),
        ],

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
        ],

        "stp_recommendation": [
            ("recommend", "stp"),
            ("suitable", "stp"),
            ("best", "stp"),
            ("choose", "stp"),
            ("select", "stp"),
        ],

        "user_role": [
            ("my", "role"),
            ("my", "account"),
            ("who", "i"),
        ],
    }

    # =====================================================
    # STRONG PATTERN MATCHING
    # =====================================================

    for intent, patterns in strong_patterns.items():

        for first, second in patterns:

            if (
                first in text
                and second in text
            ):
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
        # Keep top two intents
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

    return best_intent, round(best_score, 2)