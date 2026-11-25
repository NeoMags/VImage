"""Curated everyday tag list (~400), grouped for the sidebar "Sorted by AI" groups.

Includes every COCO category (under a friendlier name where useful) so the T1 spike can
score tag quality against COCO's human labels. COCO_TO_TAG maps COCO names onto these.
"""

GROUPS: dict[str, list[str]] = {
    "People": [
        "people", "person", "man", "woman", "child", "baby", "group of people", "crowd", "selfie", "portrait",
        "family", "couple", "friends", "wedding", "bride", "students", "worker", "athlete", "musician", "dancer",
    ],
    "Animals": [
        "dog", "cat", "bird", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "fish", "insect",
        "butterfly", "monkey", "rabbit", "duck", "chicken", "goat", "pig", "deer", "lion", "tiger", "snake",
        "turtle", "parrot", "pigeon", "camel", "buffalo", "squirrel", "aquarium", "pet",
    ],
    "Food & drink": [
        "food", "meal", "breakfast", "lunch", "dinner", "dessert", "cake", "pizza", "sandwich", "hot dog",
        "donut", "banana", "apple", "orange", "broccoli", "carrot", "fruit", "vegetables", "salad", "rice",
        "noodles", "curry", "bread", "soup", "ice cream", "chocolate", "coffee", "tea", "juice", "wine",
        "beer", "cocktail", "bottle", "wine glass", "cup", "bowl", "fork", "knife", "spoon", "restaurant",
        "street food", "cooking", "kitchen", "barbecue", "seafood", "meat", "eggs", "biscuits", "sweets",
    ],
    "Places & buildings": [
        "building", "house", "apartment building", "skyscraper", "office", "shop", "market", "mall", "school",
        "hospital", "church", "temple", "mosque", "monument", "museum", "bridge", "tower", "castle", "palace",
        "street", "road", "highway", "city", "village", "skyline", "parking lot", "airport", "train station",
        "bus stop", "stadium", "park", "playground", "garden", "farm", "factory", "warehouse", "construction site",
        "interior", "living room", "bedroom", "bathroom", "dining room", "hotel room", "classroom", "library",
        "staircase", "corridor", "balcony", "roof", "facade", "window", "door", "floor plan", "architecture",
    ],
    "Nature & outdoors": [
        "landscape", "mountain", "hill", "valley", "forest", "tree", "flower", "plant", "grass", "field",
        "desert", "beach", "sea", "ocean", "lake", "river", "waterfall", "island", "sky", "clouds", "sunset",
        "sunrise", "night sky", "stars", "moon", "rain", "snow", "fog", "rainbow", "rock", "cave", "sand",
        "countryside", "outdoors", "indoors", "night", "daytime",
    ],
    "Vehicles & transport": [
        "car", "truck", "bus", "train", "motorcycle", "bicycle", "airplane", "boat", "ship", "taxi",
        "auto rickshaw", "scooter", "traffic", "traffic light", "stop sign", "fire hydrant", "parking meter",
        "road sign", "vehicle interior", "dashboard", "tractor", "helicopter",
    ],
    "Things": [
        "chair", "couch", "bed", "dining table", "table", "desk", "bench", "toilet", "sink", "refrigerator",
        "oven", "microwave", "toaster", "tv", "laptop", "computer", "monitor", "keyboard", "mouse", "remote",
        "cell phone", "smartphone", "tablet", "camera", "headphones", "clock", "watch", "vase", "scissors",
        "teddy bear", "hair drier", "toothbrush", "book", "books", "umbrella", "handbag", "backpack",
        "suitcase", "tie", "shoes", "clothing", "dress", "jewellery", "glasses", "hat", "bag", "toy",
        "potted plant", "lamp", "furniture", "tools", "machine", "cable", "battery", "money", "credit card",
        "key", "gift", "balloon", "candle", "mirror", "painting", "sculpture", "poster", "sign",
    ],
    "Sports & activities": [
        "sports", "frisbee", "skis", "snowboard", "sports ball", "football", "cricket", "basketball",
        "tennis", "tennis racket", "baseball bat", "baseball glove", "kite", "skateboard", "surfboard",
        "swimming", "running", "cycling", "hiking", "camping", "gym", "yoga", "concert", "party",
        "festival", "celebration", "birthday", "travel", "vacation", "shopping", "meeting", "presentation",
        "conference", "graduation", "game", "video game", "music", "dance", "performance",
    ],
    "Documents & screens": [
        "document", "text", "handwriting", "notes", "receipt", "invoice", "bill", "ticket", "boarding pass",
        "id card", "passport", "certificate", "letter", "form", "contract", "menu", "page of a book",
        "printed page", "business card", "label", "barcode", "qr code", "map", "chart", "graph", "table of data",
        "spreadsheet", "presentation slide", "whiteboard", "blackboard", "diagram", "flowchart", "drawing",
        "sketch", "technical drawing", "blueprint", "plan drawing", "3d render", "screenshot",
        "phone screenshot", "computer screenshot", "chat conversation", "email", "web page", "social media post",
        "code", "error message", "app interface", "settings screen", "video call", "meme", "infographic",
        "advertisement", "logo", "icon", "scan",
    ],
    "Art & style": [
        "art", "illustration", "cartoon", "anime", "digital art", "watercolor", "photograph",
        "black and white photo", "close-up", "macro", "aerial view", "panorama", "wallpaper", "pattern",
        "texture", "abstract", "colorful", "dark photo", "blurry photo", "old photo",
    ],
}

COCO_TO_TAG = {
    "person": "person", "bicycle": "bicycle", "car": "car", "motorcycle": "motorcycle", "airplane": "airplane",
    "bus": "bus", "train": "train", "truck": "truck", "boat": "boat", "traffic light": "traffic light",
    "fire hydrant": "fire hydrant", "stop sign": "stop sign", "parking meter": "parking meter", "bench": "bench",
    "bird": "bird", "cat": "cat", "dog": "dog", "horse": "horse", "sheep": "sheep", "cow": "cow",
    "elephant": "elephant", "bear": "bear", "zebra": "zebra", "giraffe": "giraffe", "backpack": "backpack",
    "umbrella": "umbrella", "handbag": "handbag", "tie": "tie", "suitcase": "suitcase", "frisbee": "frisbee",
    "skis": "skis", "snowboard": "snowboard", "sports ball": "sports ball", "kite": "kite",
    "baseball bat": "baseball bat", "baseball glove": "baseball glove", "skateboard": "skateboard",
    "surfboard": "surfboard", "tennis racket": "tennis racket", "bottle": "bottle", "wine glass": "wine glass",
    "cup": "cup", "fork": "fork", "knife": "knife", "spoon": "spoon", "bowl": "bowl", "banana": "banana",
    "apple": "apple", "sandwich": "sandwich", "orange": "orange", "broccoli": "broccoli", "carrot": "carrot",
    "hot dog": "hot dog", "pizza": "pizza", "donut": "donut", "cake": "cake", "chair": "chair", "couch": "couch",
    "potted plant": "potted plant", "bed": "bed", "dining table": "dining table", "toilet": "toilet", "tv": "tv",
    "laptop": "laptop", "mouse": "mouse", "remote": "remote", "keyboard": "keyboard", "cell phone": "cell phone",
    "microwave": "microwave", "oven": "oven", "toaster": "toaster", "sink": "sink", "refrigerator": "refrigerator",
    "book": "book", "clock": "clock", "vase": "vase", "scissors": "scissors", "teddy bear": "teddy bear",
    "hair drier": "hair drier", "toothbrush": "toothbrush",
}

ALL_TAGS: list[str] = list(dict.fromkeys(t for tags in GROUPS.values() for t in tags))
assert set(COCO_TO_TAG.values()) <= set(ALL_TAGS)
