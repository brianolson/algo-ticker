# Algorand Ticker

```sh
#(cd .. && git clone https://github.com/algobolson/algobot.git)
#ln -s ../algobot/algobot.py .
pip install py-algorand-sdk websockets
python3 ticker.py --algod "${ALGORAND_DATA}"
```

View your ticker as:

http://localhost:8580/ticker.html
