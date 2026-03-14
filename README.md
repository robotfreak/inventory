# inventory
inventory system for your makerspace or private users with some extra features:
* bulk creation of articles for E series parts like resistors, capaciors or inductives
* pick up light. Light up the box with the item you search for
* KI picture scan. Bulk creation of unsorted articles from a picure. What is in the box
* QR Scan. Create QR-COde labels for the cheap Fichero label printer and scan the QR-Code with your smartphone camera
* Neighborhood search. Scan for items in the databases of your buddies if you are running out of stock 

## requirements

* Python3 & pip
* Flask

## installation

1. Clone this repo with 
```git clone https://github.com/robotfreak/inventory.git```
2. Change into the directory with ```cd inventory```
3. Create a python virtual environment with ```python3 -m venv .venv```
4. Activate the virtual environment with ```source .env/bin/activate```
5. Install the needed python libraries with ```pip3 install -r requirements.txt```
6. Create a sample database with ```python3 init_db.py````
7. Start the web app with ```python3 app.py```