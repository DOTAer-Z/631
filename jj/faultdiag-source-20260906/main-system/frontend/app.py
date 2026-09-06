from flask import Flask

app = Flask(__name__)

@app.route('/api/v1/test')
def test():
    return {'msg': 'ok'}

app.run(host='0.0.0.0', port=8000)