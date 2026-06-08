import os
import json
import time
import traceback

import vk_api
from dotenv import load_dotenv

load_dotenv()

USER_TOKEN = os.getenv("USER_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

PUBLISHED_FILE = "published.json"


def load_published():
    try:
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"published": []}


def save_published(data):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_attachments(post):
    """Собираем вложения в формат VK API"""
    attachments = []

    for a in post.get("attachments", []):
        t = a["type"]
        obj = a[t]

        owner_id = obj.get("owner_id")
        item_id = obj.get("id")

        if owner_id and item_id:
            attachments.append(f"{t}{owner_id}_{item_id}")

    return ",".join(attachments) if attachments else None


vk_session = vk_api.VkApi(token=USER_TOKEN)
vk = vk_session.get_api()

published = load_published()

print("BOT STARTED")

while True:
    try:
        response = vk.wall.get(
            owner_id=-GROUP_ID,
            filter="suggests",
            count=50
        )

        items = response.get("items", [])

        print(f"Found suggested posts: {len(items)}")

        for post in reversed(items):

            post_id = post["id"]

            if post_id in published["published"]:
                continue

            print(f"\nPublishing post ID: {post_id}")

            text = post.get("text", "")

            # 🔥 Анонимность
            if not post.get("signer_id"):
                text += "\n\n(Анонимный пост)"

            attachments = build_attachments(post)

            try:
                result = vk.wall.post(
                    owner_id=-GROUP_ID,
                    from_group=1,
                    signed=1,
                    message=text,
                    attachments=attachments
                )

                print("Published:", result)

                published["published"].append(post_id)
                save_published(published)

            except Exception as e:
                print("Publish error:", e)

        time.sleep(CHECK_INTERVAL)

    except Exception:
        traceback.print_exc()
        time.sleep(60)
