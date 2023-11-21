import ast
import base64
import configparser
import io
import os
import sqlite3
from uuid import uuid4

import requests
from PIL import Image, PngImagePlugin
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton

config = configparser.ConfigParser()
config.read("config.ini")

API_ID = config.get("pyrogram", "API_ID")
API_HASH = config.get("pyrogram", "API_HASH")
BOT_TOKEN = config.get("pyrogram", "BOT_TOKEN")
API_URL = config.get("pyrogram", "API_URL")
ADMINS = ast.literal_eval(config.get('stable', 'ADMINS'))
COLAB_URL = config.get("stable", "API_URL")

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# Maximum number of images to save per user
MAX_IMAGES_PER_USER = 10

selected_model = None

# Dictionary of available aspect ratios and their corresponding resolutions
ASPECT_RATIO_OPTIONS = {
    "4:3": (1024, 768),
    "4:5": (1024, 1280),
    "5:4": (1280, 1024),
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "16:10": (1280, 800),
    "HD Wide": (1706, 960),
    "HD Portrait": (960, 1706)
    # Add more aspect ratios and resolutions as needed
}


# Initialize SQLite database
def initialize_database():
    with sqlite3.connect('user_settings.db') as conn:
        cursor = conn.cursor()

        # Create a table to store user settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                resolution_width INTEGER,
                resolution_height INTEGER
            )
        ''')

        conn.commit()


# Fetch user resolution from the database
def get_user_resolution(user_id):
    with sqlite3.connect('user_settings.db') as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT resolution_width, resolution_height FROM user_settings WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()

    return result if result else (1024, 1280)


# Save user resolution to the database
def save_user_resolution(user_id, resolution):
    with sqlite3.connect('user_settings.db') as conn:
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO user_settings (user_id, resolution_width, resolution_height)
            VALUES (?, ?, ?)
        ''', (user_id, resolution[0], resolution[1]))

        conn.commit()


# Call the initialize_database function to set up the SQLite database
initialize_database()


# Handler for incoming messages
@app.on_message(filters.text & filters.private, group=2)
async def echo(client, message: Message):
    user_id = message.from_user.id

    # Fetch user settings from the database
    resolution = get_user_resolution(user_id)

    gen_message = await message.reply(f"Generating your request with resolution {resolution[0]}x{resolution[1]}...")

    url = COLAB_URL

    payload = {
        "prompt": message.text,
        "sampler_index": "DPM++ 2M Karras",
        "steps": 25,
        "width": resolution[0],
        "height": resolution[1],
        "batch_size": 1,
        "negative_prompt": "(worst quality, low quality, normal quality, lowres, low details, oversaturated, undersaturated, overexposed, underexposed, grayscale, bw, bad photo, bad photography, bad art:1.4), (watermark, signature, text font, username, error, logo, words, letters, digits, autograph, trademark, name:1.2), (blur, blurry, grainy), morbid, ugly, asymmetrical, mutated malformed, mutilated, poorly lit, bad shadow, draft, cropped, out of frame, cut off, censored, jpeg artifacts, out of focus, glitch, duplicate, (airbrushed, cartoon, anime, semi-realistic, cgi, render, blender, digital art, manga, amateur:1.3), (3D ,3D Game, 3D Game Scene, 3D Character:1.1), (bad hands, bad anatomy, bad body, bad face, bad teeth, bad arms, bad legs, deformities:1.3)"
    }

    await message.reply_chat_action(enums.ChatAction.TYPING)

    # Send request to the image generation service
    response = requests.post(url=f'{url}/sdapi/v1/txt2img', json=payload)
    r = response.json()

    user_output_folder = f"outputs/{user_id}"

    files = []
    for i in r['images']:
        # Open the image from base64 and process it
        image = Image.open(io.BytesIO(base64.b64decode(i.split(",", 1)[0])))

        png_payload = {
            "image": "data:image/png;base64," + i
        }
        # Send request to obtain PNG info
        response2 = requests.post(url=f'{url}/sdapi/v1/png-info', json=png_payload)

        # Create the user-specific output directory if it doesn't exist
        os.makedirs(user_output_folder, exist_ok=True)

        output_file_name = f"{user_output_folder}/{uuid4()}.png"
        files.append(output_file_name)

        # Add metadata to the image
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("parameters", response2.json().get("info"))
        image.save(output_file_name, pnginfo=pnginfo)

    await gen_message.delete()
    await message.reply_chat_action(enums.ChatAction.UPLOAD_PHOTO)

    media_items = []
    for file in files:
        # Use the message as the description for each photo
        description = message.text
        media_items.append(InputMediaPhoto(file, caption=description))

    # Send the generated images back to the user as a media group
    await message.reply_media_group(media_items)

    # Remove excessive images if there are more than MAX_IMAGES_PER_USER
    user_files = os.listdir(user_output_folder)
    if len(user_files) > MAX_IMAGES_PER_USER:
        user_files.sort()
        for file in user_files[:-MAX_IMAGES_PER_USER]:
            file_path = os.path.join(user_output_folder, file)
            os.remove(file_path)


# Command handler to set the image generation service URL
@app.on_message(filters.private & filters.command('seturl'), group=0)
async def set_colab_url(bot, message: Message):
    global COLAB_URL
    found_url = ' '.join(message.command[1:])
    COLAB_URL = found_url
    await message.reply(f'SD URL set to {COLAB_URL}')
    await message.stop_propagation()


# Command handler to check the current image generation service URL
@app.on_message(filters.private & filters.command('checkurl'), group=0)
async def check_colab_url(bot, message: Message):
    global COLAB_URL
    await message.reply(f'SD URL is set to {COLAB_URL}')
    await message.stop_propagation()


# Command handler to get the available models and display them as buttons
@app.on_message(filters.private & filters.command('models'), group=0)
async def get_models(bot, message: Message):
    response = requests.get(url=f'{COLAB_URL}/sdapi/v1/sd-models')
    models = response.json()

    buttons = []
    for model in models:
        title = model['title']
        model_name = model['model_name']
        button = InlineKeyboardButton(title, callback_data=f"model_{model_name}")  # Updated callback_data format
        buttons.append([button])  # Place each button in a new list

    reply_markup = InlineKeyboardMarkup(buttons)  # Use the list of buttons directly
    await message.reply("Select a model:", reply_markup=reply_markup)
    await message.stop_propagation()


# Handle the button callback for both models and aspect ratio selection
@app.on_callback_query()
async def handle_button_callback(bot, callback_query):
    global selected_model

    if callback_query.data.startswith("model_"):
        # Extract the model name from callback_data
        selected_model = callback_query.data.replace("model_", "")
        await callback_query.answer()
        await callback_query.message.edit_text(f"You selected: {selected_model}")

        # Switch the model
        option_payload = {
            "sd_model_checkpoint": selected_model
        }
        response = requests.post(url=f'{COLAB_URL}/sdapi/v1/options', json=option_payload)
        if response.status_code == 200:
            await callback_query.message.reply(f"Model switched to: {selected_model}")
        else:
            await callback_query.message.reply("Failed to switch the model. Please try again.")
    elif callback_query.data.startswith("aspect_"):
        # Handle aspect ratio selection (as before)
        aspect_ratio = callback_query.data.replace("aspect_", "").replace("x", ":")
        resolution = ASPECT_RATIO_OPTIONS.get(aspect_ratio, (1024, 1280))

        await callback_query.answer()
        await callback_query.message.edit_text(f"Aspect ratio set to: {aspect_ratio} ({resolution[0]}x{resolution[1]})")

        # Set the resolution for the current user
        user_id = callback_query.from_user.id
        save_user_resolution(user_id, resolution)


# Command handler to provide aspect ratio options
@app.on_message(filters.private & filters.command('aspect'), group=0)
async def aspect_ratio_options(bot, message: Message):
    aspect_buttons = []
    for aspect_ratio, resolution in ASPECT_RATIO_OPTIONS.items():
        button_text = f"{aspect_ratio} ({resolution[0]}x{resolution[1]})"
        callback_data = f"aspect_{aspect_ratio.replace(':', 'x')}"
        aspect_button = InlineKeyboardButton(button_text, callback_data=callback_data)
        aspect_buttons.append([aspect_button])

    reply_markup = InlineKeyboardMarkup(aspect_buttons)
    await message.reply("Select an aspect ratio:", reply_markup=reply_markup)
    await message.stop_propagation()


# Handle the button callback for aspect ratio selection
@app.on_callback_query()
async def handle_button_callback(bot, callback_query):
    global selected_model

    if callback_query.data.startswith("aspect_"):
        aspect_ratio = callback_query.data.replace("aspect_", "").replace("x", ":")
        resolution = ASPECT_RATIO_OPTIONS.get(aspect_ratio, (1024, 1280))

        await callback_query.answer()
        await callback_query.message.edit_text(f"Aspect ratio set to: {aspect_ratio} ({resolution[0]}x{resolution[1]})")

        # Set the resolution for the current user
        user_id = callback_query.from_user.id
        save_user_resolution(user_id, resolution)


# Command handler to get the current aspect ratio
@app.on_message(filters.private & filters.command('getaspect'), group=0)
async def get_aspect_ratio(bot, message: Message):
    user_id = message.from_user.id
    resolution = get_user_resolution(user_id)
    aspect_ratio = f"{resolution[0]}:{resolution[1]}"
    await message.reply(f'Current aspect ratio: {aspect_ratio}')
    await message.stop_propagation()


app.run()  # Start the bot and idle()
