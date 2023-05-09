import os
import json
import openai
import requests
from flask import Flask, request, jsonify, render_template, session
from googleapiclient.discovery import build
import aiohttp
from aiohttp import ClientSession
from aiohttp import ClientConnectorError
from google.auth import exceptions
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import spacy.cli
import spacy
import uuid
from flask_socketio import SocketIO, emit
import threading
import asyncio
import async_timeout
from openai.error import OpenAIError, RateLimitError

app = Flask(__name__)

if not spacy.util.is_package('en_core_web_sm'):
    spacy.cli.download('en_core_web_sm')

app.secret_key = "super secret key"
nlp = spacy.load("en_core_web_sm")
socketio = SocketIO(app, cors_allowed_origins="*")
search_status = {}

stop_search_flag = False

API_KEY = "AIzaSyC2WO6N2kL2sR8RlRULKTGjpZbywTb-Ow8"
CSE_ID = "805eea5ee29654a3f"
OPENAI_API_KEY = "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"
SEARCH_TERMS = ["fraud", "crime", "money laundering"]
EXTRA_TOKENS = 50
MAX_TOKENS = 4097

openai.api_key = OPENAI_API_KEY


def set_stop_search(value):
    global stop_search_flag
    stop_search_flag = value


def count_tokens(text):
    doc = nlp(text)
    return len(doc)


def calculate_total_tokens(text):
    doc = nlp(text)
    tokens = [token.text for token in doc]
    return len(tokens)


async def search(query, num_results):
    results = []
    start = 1
    session_id = uuid.uuid4().hex
    search_status[session_id] = "searching"

    async with aiohttp.ClientSession() as session:
        while len(results) < num_results:
            try:
                async with session.get('https://www.googleapis.com/customsearch/v1', params={
                    'key': API_KEY,
                    'cx': CSE_ID,
                    'q': query,
                    'start': start
                }) as response:
                    res = await response.json()
                    if "items" in res:
                        results.extend(res["items"])
                        start += 10
                    else:
                        break
            except ClientConnectorError:
                print(f"Connection error occurred during the request to the Google Search API for the query: {query}")
                return []
            except asyncio.TimeoutError:
                print(f"Timeout occurred during the request to the Google Search API for the query: {query}")
                return []
    return results[:num_results]


def clean_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["header", "footer", "nav", "aside", "script", "style"]):
        tag.decompose()
    return soup.get_text()


async def get_cleaned_page(session, url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as res:
            return clean_text(await res.text())


def truncate_text(text, max_tokens):
    if len(text) > nlp.max_length:
        text = text[:nlp.max_length]

    doc = nlp(text)

    # Calculate the number of tokens for the GPT model
    gpt_tokens = sum([len(token.text_with_ws.strip()) + 1 for token in doc])

    # Truncate text based on GPT model's token limit
    if gpt_tokens > max_tokens:
        truncated_gpt_tokens = 0
        truncated_doc = []
        for token in doc:
            token_len = len(token.text_with_ws.strip()) + 1
            if truncated_gpt_tokens + token_len <= max_tokens:
                truncated_gpt_tokens += token_len
                truncated_doc.append(token)
            else:
                break
        return "".join(token.text_with_ws for token in truncated_doc).strip()

    return text


def query_chatgpt(text, first_name, last_name):
    questions = f"1. Is {first_name} {last_name} mentioned in the text?\n" \
                f"2. Is {first_name} {last_name} described in the text to be involved in fraud?\n" \
                f"3. Is {first_name} {last_name} described in the text to be involved in criminal activity?\n" \
                f"4. Is {first_name} {last_name} described in the text to be involved in money laundering?\n" \
                f"5. Summarize what this text says about {first_name} {last_name} in 50 words."
    messages = [
        {"role": "system", "content": f"You answer only with yes and no, without providing any explanations. You will answer questions about the following text \n {text}"},
        {"role": "user", "content": f"Answer to these questions about the text from the previous prompt:\n{questions}"}
    ]
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.5  # set the temperature.
        )
    except RateLimitError:
        print("Rate limit exceeded on OpenAI API. Retrying after a delay...")
        asyncio.sleep(5)  # Wait for 5 seconds before retrying
        return query_chatgpt(text, first_name, last_name)  # Retry the request
    except OpenAIError as e:
        print(f"An error occurred while making a request to OpenAI: {e}")
        return []  # Return an empty response in case of error
    print(response)
    return response.choices[0].message.content.strip().split("\n")


def parse_chatgpt_response(response):
    weights = []

    for i in range(4):  # Iterate for the first 4 questions
        if i < len(response):
            answer = response[i]
            if i == 0:  # for Q1
                if "Yes" in answer or "yes" in answer:
                    weights.append(1.0)
                else:
                    weights.append(0.2)
            else:  # for Q2-Q4
                if "Yes" in answer or "yes" in answer:
                    weights.append(1.0)
                elif "explicitly" in answer or "indirectly" in answer:
                    weights.append(0.6)
                else:
                    weights.append(0)
        else:
            weights.append(0)  # Add 0 weight for missing responses

    # Get the summary from the response if available, otherwise use an empty string
    summary = response[4] if len(response) > 4 else ""

    return weights, summary


def calculate_score(weights):
    q1 = weights[0]
    q2_q4 = sum(weights[1:])
    score = int(100 * q1 * q2_q4 / 3)
    return score

async def search_person(first_name, last_name, num_results, strict_search):
    global stop_search_flag
    # Reset the stop search flag at the beginning of a search
    set_stop_search(False)

    first_name = request.args.get("firstName")
    last_name = request.args.get("lastName")
    num_results = int(request.args.get("numResults"))
    strict_search = request.args.get("strictSearch") == "true"

    if strict_search:
        queries = [f'"{first_name} {last_name}" "{term}"' for term in SEARCH_TERMS]
    else:
        queries = [f'{first_name} {last_name} {term}' for term in SEARCH_TERMS]

    all_results = []
    total_results = 0  # Initialize the counter

    with ThreadPoolExecutor() as executor:
        loop = asyncio.get_event_loop()

        for query in queries:
            search_results = search(query, num_results)
            search_results = await search_results
            for result in search_results:
                # If the stop search flag is True, or the counter reached num_results, break the loop
                if stop_search_flag or total_results >= num_results:
                    break

                url = result["link"]
                title = result["title"]
                snippet = result["snippet"]

                try:
                    # Create an aiohttp session
                    async with aiohttp.ClientSession() as session:
                        async with async_timeout.timeout(10):  # Set a timeout of 10 seconds
                            cleaned_text = await get_cleaned_page(session, url)
                except asyncio.TimeoutError:
                    print(f"Timeout occurred while fetching the URL: {url}")
                    continue  # Skip this URL and proceed to the next one

                truncated_text = truncate_text(cleaned_text, 4096)

                # Calculate tokens
                total_tokens = calculate_total_tokens(
                    truncated_text) + calculate_total_tokens(first_name + last_name) + EXTRA_TOKENS

                # Truncate the text further if necessary
                if total_tokens > MAX_TOKENS:
                    extra_tokens = total_tokens - MAX_TOKENS
                    truncated_text = truncate_text(truncated_text, extra_tokens)

                future_chatgpt_response = loop.run_in_executor(executor, query_chatgpt, truncated_text, first_name, last_name)
                chatgpt_response = await future_chatgpt_response
                weights, summary = parse_chatgpt_response(chatgpt_response)
                score = calculate_score(weights)
                keywords = ", ".join(
                    [term for i, term in enumerate(SEARCH_TERMS) if weights[i + 1] > 0])

                socketio.emit('new_result', {
                    "title": title,
                    "score": score,
                    "url": url,
                    "snippet": snippet,
                    "summary": summary,
                    "keywords": keywords,
                    "status": "TRUE" if score > 0 else "FALSE"
                })

                total_results += 1  # Update the counter

            # If the stop search flag is True, or the counter reached num_results, break the loop
            if stop_search_flag or total_results >= num_results:
                break

    return '', 204



@app.route("/search", methods=["GET"])
async def search_route():
    first_name = request.args.get("firstName")
    last_name = request.args.get("lastName")
    num_results = int(request.args.get("numResults"))
    strict_search = request.args.get("strictSearch") == "true"

    await search_person(first_name, last_name, num_results, strict_search)

    return '', 204


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stop')
def stop_search():
    set_stop_search(True)
    return "Search stopped"


if __name__ == "__main__":
    socketio.run(app, debug=True)
