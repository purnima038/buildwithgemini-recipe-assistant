"""Seed script to populate initial recipe data in Firestore."""
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-03-55eeaf0ffe76"

SAMPLE_RECIPES = [
    {
        "id": "garlic-butter-salmon",
        "title": "Garlic Butter Salmon",
        "cuisine": "American",
        "prep_time_minutes": 20,
        "servings": 2,
        "ingredients": ["2 salmon fillets", "3 cloves garlic minced", "2 tbsp butter", "1 tbsp lemon juice", "1 tbsp fresh parsley"],
        "instructions": [
            "Pat salmon dry and season with salt and pepper.",
            "Melt butter in skillet over medium-high heat.",
            "Add garlic and saute until fragrant.",
            "Cook salmon skin-side down for 4 minutes, flip and cook 3 minutes more.",
            "Drizzle with lemon juice and garnish with parsley."
        ],
        "tags": ["quick", "keto", "seafood", "dinner"]
    },
    {
        "id": "pasta-carbonara",
        "title": "Classic Spaghetti Carbonara",
        "cuisine": "Italian",
        "prep_time_minutes": 25,
        "servings": 4,
        "ingredients": ["400g spaghetti", "150g guanciale or pancetta", "4 large egg yolks", "100g Pecorino Romano cheese", "Black pepper"],
        "instructions": [
            "Boil spaghetti in salted water until al dente.",
            "Crisp guanciale in a large skillet.",
            "Whisk egg yolks with grated Pecorino Romano and coarse black pepper.",
            "Toss hot pasta in skillet with guanciale off heat.",
            "Stir in egg mixture quickly with pasta water to form creamy sauce."
        ],
        "tags": ["classic", "pasta", "quick", "dinner"]
    },
    {
        "id": "avocado-toast-egg",
        "title": "Poached Egg Avocado Toast",
        "cuisine": "Breakfast",
        "prep_time_minutes": 15,
        "servings": 1,
        "ingredients": ["1 slice sourdough bread", "1 ripe avocado", "1 large egg", "1 tsp lemon juice", "Red pepper flakes", "Salt and pepper"],
        "instructions": [
            "Toast sourdough bread until golden.",
            "Mash avocado with lemon juice, salt, and red pepper flakes.",
            "Poach egg in gently simmering water with a dash of vinegar for 3 minutes.",
            "Spread avocado mixture on toast and top with poached egg."
        ],
        "tags": ["breakfast", "easy", "vegetarian", "healthy"]
    },
    {
        "id": "thai-green-curry",
        "title": "Thai Green Chicken Curry",
        "cuisine": "Thai",
        "prep_time_minutes": 35,
        "servings": 4,
        "ingredients": ["500g chicken thigh cut into chunks", "2 tbsp green curry paste", "400ml coconut milk", "1 cup bamboo shoots", "Thai basil leaves", "Fish sauce"],
        "instructions": [
            "Fry green curry paste in 2 tbsp of coconut milk until aromatic.",
            "Add chicken pieces and cook until lightly browned.",
            "Pour in remaining coconut milk and bamboo shoots, simmer for 15 minutes.",
            "Season with fish sauce and stir in Thai basil before serving with jasmine rice."
        ],
        "tags": ["spicy", "curry", "asian", "gluten-free"]
    }
]


def seed_database():
    print(f"Connecting to Firestore project: {PROJECT_ID}")
    db = firestore.Client(
        project=PROJECT_ID,
        client_options={"quota_project_id": PROJECT_ID},
    )
    collection_ref = db.collection("recipes")

    for recipe in SAMPLE_RECIPES:
        doc_id = recipe["id"]
        data = {k: v for k, v in recipe.items() if k != "id"}
        collection_ref.document(doc_id).set(data)
        print(f"Seeded recipe: {doc_id} -> {recipe['title']}")

    print("Firestore seeding complete!")


if __name__ == "__main__":
    seed_database()
