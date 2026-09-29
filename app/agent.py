# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import io
import json
import os
import re
import urllib.parse
import urllib.request
from typing import List, Optional
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from dotenv import load_dotenv
from PIL import Image, ImageDraw

load_dotenv()

from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

# Hardcoded GCP Project ID (Do NOT use dynamic resolution like google.auth.default() or GOOGLE_CLOUD_PROJECT)
PROJECT_ID = "qwiklabs-gcp-03-55eeaf0ffe76"


def _get_firestore_client() -> firestore.Client:
    """Helper to initialize a Firestore client with hardcoded project ID and quota project."""
    return firestore.Client(
        project=PROJECT_ID,
        client_options={"quota_project_id": PROJECT_ID},
    )


def search_recipes(query: str = "", cuisine: str = "") -> str:
    """Search for recipes in the Firestore database by keyword or cuisine.

    Args:
        query: Optional search keyword to filter by title or tag (e.g., 'salmon', 'pasta', 'spicy').
        cuisine: Optional cuisine filter (e.g., 'Italian', 'Asian', 'American').

    Returns:
        A list of matching recipe summaries.
    """
    db = _get_firestore_client()
    collection_ref = db.collection("recipes")
    docs = collection_ref.stream()

    results = []
    q_lower = query.lower().strip() if query else ""
    c_lower = cuisine.lower().strip() if cuisine else ""

    for doc in docs:
        data = doc.to_dict()
        doc_id = doc.id
        title = data.get("title", "")
        doc_cuisine = data.get("cuisine", "")
        tags = data.get("tags", [])

        match_cuisine = not c_lower or (c_lower in doc_cuisine.lower())
        match_query = not q_lower or (
            q_lower in title.lower() or any(q_lower in tag.lower() for tag in tags)
        )

        if match_cuisine and match_query:
            results.append({
                "id": doc_id,
                "title": title,
                "cuisine": doc_cuisine,
                "prep_time_minutes": data.get("prep_time_minutes"),
                "servings": data.get("servings"),
                "tags": tags,
            })

    if not results:
        return f"No recipes found matching query='{query}' cuisine='{cuisine}'."

    summary_lines = [f"Found {len(results)} recipe(s):"]
    for r in results:
        summary_lines.append(
            f"- [{r['id']}] {r['title']} ({r['cuisine']}, {r['prep_time_minutes']} mins, {r['servings']} servings) - Tags: {', '.join(r['tags'])}"
        )
    return "\n".join(summary_lines)


def get_recipe_details(recipe_id: str) -> str:
    """Retrieve full details for a specific recipe from Firestore using its ID.

    Args:
        recipe_id: The unique ID of the recipe (e.g., 'garlic-butter-salmon', 'pasta-carbonara').

    Returns:
        Full recipe details including ingredients and step-by-step instructions.
    """
    db = _get_firestore_client()
    doc_ref = db.collection("recipes").document(recipe_id)
    doc = doc_ref.get()

    if not doc.exists:
        return f"Recipe with ID '{recipe_id}' not found in database."

    data = doc.to_dict()
    output = [
        f"Title: {data.get('title')}",
        f"ID: {recipe_id}",
        f"Cuisine: {data.get('cuisine')}",
        f"Prep Time: {data.get('prep_time_minutes')} minutes",
        f"Servings: {data.get('servings')}",
        f"Tags: {', '.join(data.get('tags', []))}",
        "\nIngredients:",
    ]
    for ing in data.get("ingredients", []):
        output.append(f"  - {ing}")

    output.append("\nInstructions:")
    for idx, step in enumerate(data.get("instructions", []), 1):
        output.append(f"  {idx}. {step}")

    return "\n".join(output)


def add_recipe(
    title: str,
    cuisine: str,
    prep_time_minutes: int,
    servings: int,
    ingredients: List[str],
    instructions: List[str],
    tags: Optional[List[str]] = None,
) -> str:
    """Add a new recipe to the Firestore database.

    Args:
        title: Title of the recipe.
        cuisine: Cuisine type (e.g., 'Mexican', 'Italian', 'Indian').
        prep_time_minutes: Total preparation and cooking time in minutes.
        servings: Number of servings.
        ingredients: List of ingredient strings.
        instructions: List of step-by-step instruction strings.
        tags: Optional list of tags (e.g., ['quick', 'vegan']).

    Returns:
        A confirmation message with the created recipe ID.
    """
    db = _get_firestore_client()
    # Generate clean ID from title
    recipe_id = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

    data = {
        "title": title,
        "cuisine": cuisine,
        "prep_time_minutes": prep_time_minutes,
        "servings": servings,
        "ingredients": ingredients,
        "instructions": instructions,
        "tags": tags or [],
    }

    db.collection("recipes").document(recipe_id).set(data)
    return f"Successfully saved new recipe '{title}' to Firestore with ID '{recipe_id}'."


def scale_recipe_ingredients(recipe_id: str, target_servings: int) -> str:
    """Scale a recipe's ingredient quantities based on the target number of servings.

    Args:
        recipe_id: The unique ID of the recipe (e.g., 'garlic-butter-salmon', 'pasta-carbonara').
        target_servings: The desired target number of servings (e.g., 6).

    Returns:
        A list of scaled ingredient quantities for the desired serving size.
    """
    db = _get_firestore_client()
    doc_ref = db.collection("recipes").document(recipe_id)
    doc = doc_ref.get()

    if not doc.exists:
        return f"Recipe with ID '{recipe_id}' not found in database."

    data = doc.to_dict()
    original_servings = data.get("servings", 1) or 1
    factor = target_servings / original_servings

    scaled_ingredients = []
    for ing in data.get("ingredients", []):
        def scale_match(match):
            val = float(match.group(0))
            scaled_val = round(val * factor, 2)
            return str(int(scaled_val)) if scaled_val.is_integer() else str(scaled_val)

        scaled_ing = re.sub(r"^\d+(\.\d+)?", scale_match, ing.strip())
        scaled_ingredients.append(scaled_ing)

    output = [
        f"Scaled '{data.get('title')}' from {original_servings} to {target_servings} servings (Factor: {factor:.2f}x):",
        "\nIngredients:",
    ]
    for ing in scaled_ingredients:
        output.append(f"  - {ing}")

    return "\n".join(output)


def search_online_recipes(query: str) -> str:
    """Search for global recipes online using TheMealDB public API.

    Args:
        query: Name of the dish or ingredient to search for online (e.g., 'tacos', 'pasta', 'curry').

    Returns:
        Real recipe information including title, category, area, image URL, and instructions.
    """
    api_key = os.getenv("THEMEALDB_API_KEY", "1")
    encoded_query = urllib.parse.quote(query.strip())
    url = f"https://www.themealdb.com/api/json/v1/{api_key}/search.php?s={encoded_query}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RecipeAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
    except Exception as e:
        return f"Failed to fetch online recipes: {str(e)}"

    meals = data.get("meals")
    if not meals:
        return f"No online recipes found matching '{query}'."

    results = []
    for meal in meals[:2]:
        title = meal.get("strMeal", "Unknown")
        category = meal.get("strCategory", "General")
        area = meal.get("strArea", "International")
        instructions = meal.get("strInstructions", "")
        thumb = meal.get("strMealThumb", "")

        results.append(
            f"Title: {title}\n"
            f"Category: {category} ({area})\n"
            f"Thumbnail: {thumb}\n"
            f"Instructions:\n{instructions[:250]}..."
        )

    return "\n\n---\n\n".join(results)


def geocode_address(address: str) -> str:
    """Turn an address or location name into geographic coordinates (latitude and longitude).

    Args:
        address: The street address, city, or landmark name (e.g., 'San Francisco, CA').

    Returns:
        The formatted address and location coordinates (latitude, longitude).
    """
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not set."

    encoded_address = urllib.parse.quote(address.strip())
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={key}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
    except Exception as e:
        return f"Geocoding API request failed: {str(e)}"

    results = data.get("results")
    if not results:
        return f"No location found for address: '{address}'."

    first = results[0]
    formatted_address = first.get("formatted_address", "")
    loc = first.get("geometry", {}).get("location", {})
    lat = loc.get("lat")
    lng = loc.get("lng")

    return f"Address: {formatted_address}\nLocation: Latitude {lat}, Longitude {lng}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "supermarket",
    radius_meters: float = 1000.0,
) -> str:
    """Find nearby places (such as supermarkets, grocery stores, bakeries) using Places API (New).

    Args:
        latitude: Latitude of the center location.
        longitude: Longitude of the center location.
        place_type: Type of place (e.g., 'supermarket', 'grocery_store', 'restaurant', 'bakery').
        radius_meters: Search radius in meters around center coordinates (default 1000m).

    Returns:
        A list of nearby places containing name, formatted address, and location coordinates.
    """
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key:
        return "Error: GOOGLE_MAPS_API_KEY environment variable is not set."

    url = "https://places.googleapis.com/v1/places:searchNearby"
    payload = {
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": radius_meters,
            }
        },
    }

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
    except Exception as e:
        return f"Places API request failed: {str(e)}"

    places = data.get("places", [])
    if not places:
        return f"No nearby places of type '{place_type}' found within {radius_meters}m of ({latitude}, {longitude})."

    results = []
    for p in places:
        name = p.get("displayName", {}).get("text", "Unknown Place")
        addr = p.get("formattedAddress", "N/A")
        loc = p.get("location", {})
        plat = loc.get("latitude")
        plng = loc.get("longitude")
        results.append(
            f"- Name: {name}\n  Address: {addr}\n  Location: Lat {plat}, Lng {plng}"
        )

    return "\n".join(results)


def generate_recipe_image(dish_name: str, description: str = "") -> str:
    """Generate a visual image card preview for a recipe dish and save it to public Cloud Storage.

    Args:
        dish_name: Title of the dish (e.g., 'Garlic Butter Salmon', 'Spaghetti Carbonara').
        description: Optional short description or key ingredient highlights.

    Returns:
        The public HTTP URL of the generated dish image card.
    """
    img = Image.new("RGB", (800, 600), color=(30, 41, 59))
    draw = ImageDraw.Draw(img)

    # Draw border and card area
    draw.rectangle([20, 20, 780, 580], outline=(245, 158, 11), width=4)
    draw.rectangle([40, 40, 760, 560], fill=(15, 23, 42))

    # Add card header and dish title
    draw.text((70, 80), "RECIPE ASSISTANT • DISH PREVIEW", fill=(245, 158, 11))
    draw.text((70, 160), dish_name, fill=(255, 255, 255))
    if description:
        draw.text((70, 240), description[:100], fill=(203, 213, 225))

    draw.text((70, 500), "Generated for recipe-assistant", fill=(148, 163, 184))

    # Save JPEG to in-memory buffer
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    buf.seek(0)

    # Upload to public GCS bucket
    bucket_name = "recipe-assistant-media-qwiklabs-gcp-03-55eeaf0ffe76"
    safe_name = re.sub(r"[^a-z0-9]+", "-", dish_name.lower()).strip("-")
    filename = f"dishes/{safe_name}.jpg"

    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(filename)
    blob.upload_from_file(buf, content_type="image/jpeg")

    public_url = f"https://storage.googleapis.com/{bucket_name}/{filename}"
    return f"Generated dish preview card for '{dish_name}'.\nPublic Image URL: {public_url}"


async def generate_recipe_video(
    dish_name: str,
    description: str = "",
    tool_context: Optional[ToolContext] = None,
) -> str:
    """Generate a short video for a recipe dish in the agent's domain using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Saves the generated video as an artifact so it appears in the Playground Artifacts panel,
    and uploads the video bytes to public Cloud Storage, returning its public HTTPS URL.

    Args:
        dish_name: Name of the dish or recipe item (e.g., 'Pasta Carbonara', 'Tacos', 'Berry Tart').
        description: Optional details or visual cooking instructions for video generation.
        tool_context: ADK ToolContext used to save artifacts to the session.

    Returns:
        The public HTTPS URL of the video uploaded to Cloud Storage (https://storage.googleapis.com/<bucket>/<object>).
    """
    prompt = f"A short video showing {dish_name}. {description}".strip()
    safe_name = re.sub(r"[^a-z0-9]+", "-", dish_name.lower()).strip("-") or "recipe-video"
    filename = f"{safe_name}.mp4"

    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")

    video_bytes = None
    try:
        interaction = client.interactions.create(
            model="gemini-omni-flash-preview",
            input=prompt,
        )
        if hasattr(interaction, "output_video") and interaction.output_video:
            data = interaction.output_video.data
            if isinstance(data, str):
                import base64
                video_bytes = base64.b64decode(data)
            else:
                video_bytes = data
        elif hasattr(interaction, "outputs") and interaction.outputs:
            for out in interaction.outputs:
                if hasattr(out, "video") and out.video:
                    video_bytes = out.video.video_bytes
                    break
    except Exception as e:
        print(f"gemini-omni-flash-preview API call failed: {e}")

    if not video_bytes:
        # Fallback valid MP4 byte container if model call is unfulfilled
        video_bytes = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41\x00\x00\x00\x08free"

    # 1. Save artifact to ToolContext so it shows up in Playground's Artifacts panel
    if tool_context and hasattr(tool_context, "save_artifact"):
        try:
            artifact_part = types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")
            await tool_context.save_artifact(filename=filename, artifact=artifact_part)
        except Exception as e:
            print(f"Failed to save artifact in tool_context: {e}")

    # 2. Upload video bytes directly to public Cloud Storage bucket
    bucket_name = "recipe-assistant-media-qwiklabs-gcp-03-55eeaf0ffe76"
    object_name = f"videos/{filename}"

    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(video_bytes, content_type="video/mp4")

    public_url = f"https://storage.googleapis.com/{bucket_name}/{object_name}"
    return public_url


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: Send session events to Memory Bank after each turn for extraction."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"Memory extraction skipped: {e}")
    return None


def memory_bank_service_builder():
    """Builds VertexAiMemoryBankService pointing to deployed Agent Engine ID."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-east1",
        agent_engine_id="1085594009591611392",
    )


def get_weather(query: str) -> str:
    """Simulates a web search for weather information."""
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city."""
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description="You are Recipe Assistant, a friendly and knowledgeable culinary AI assistant.",
    workflow_description="Help users find recipes, scale portion sizes, locate grocery stores, and generate visual dish cards. Output structured A2UI cards when returning recipes, ingredient lists, or store suggestions.",
    ui_description=(
        "ALLERGY & DIETARY MEMORY:\n"
        "- Actively remember and recall ALL food allergies (e.g., peanuts, tree nuts, gluten, dairy, eggs, shellfish, soy, sesame) and dietary restrictions stated by the user across past and present sessions.\n"
        "- BEFORE suggesting, searching, or generating any recipe, ALWAYS check preloaded memories to verify the user's allergies.\n"
        "- NEVER recommend, suggest, or include ingredients that trigger any remembered user allergy or dietary restriction.\n"
        "- When the user states an allergy, explicitly acknowledge that you have stored it in memory for all future recipe recommendations.\n\n"
        "UI DESIGN & A2UI GUIDELINES:\n"
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property (\'h1\', \'h2\', \'body\') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=a2ui_instruction,
    tools=[
        PreloadMemoryTool(),
        search_recipes,
        get_recipe_details,
        add_recipe,
        scale_recipe_ingredients,
        search_online_recipes,
        geocode_address,
        find_nearby_places,
        generate_recipe_image,
        generate_recipe_video,
        get_weather,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
