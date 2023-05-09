Here is the list of required packages that need to be installed using pip3:

Make sure you have Python 3.6 or later installed on your computer.

Clone or download the repository to your local machine.
Open a command prompt or terminal window and navigate to the project directory.
Create a virtual environment by running the following command: python3 -m venv env.
Activate the virtual environment by running the following command:
For Mac/Linux: source env/bin/activate
For Windows: env\Scripts\activate
Install the required Python packages by running the following command: pip install -r requirements.txt

or do it manually

----
nltk
google-api-python-client
google-auth
google-auth-oauthlib
google-auth-httplib2
Flask
Flask-SocketIO
requests
beautifulsoup4
openai
----
spacy
en-core-web-sm
-->
pip install -U pip setuptools wheel
pip install -U spacy
python -m spacy download en_core_web_sm


Set the necessary environment variables:
API_KEY: Your Google Custom Search Engine API key
CSE_ID: Your Google Custom Search Engine ID
OPENAI_API_KEY: Your OpenAI API key

Start the Flask app by running the following command: flask run.
Open a web browser and navigate to http://localhost:5000 to access the web application.