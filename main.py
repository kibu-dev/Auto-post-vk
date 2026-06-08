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

if not USER_TOKEN:
    raise Exception("USER_TOKEN not found")

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


published = load_published()

vk_session = vk_api.VkApi(token=USER_TOKEN)
vk = vk_session.get_api()

print("BOT STARTED")

while True:
    try:
        suggests = vk.wall.get(
            owner_id=-GROUP_ID,
            filter="suggests",
            count=100
        )

        items = suggests.get("items", [])

        print(f"Found suggested posts: {len(items)}")

        for post in reversed(items):

            post_id = post["id"]

            if post_id in published["published"]:
                continue

            print(f"Publishing suggested post {post_id}")

            try:
                result = vk.wall.post(
                    owner_id=-GROUP_ID,
                    from_group=1,
                    post_id=post_id
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
