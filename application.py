#!/usr/bin/env python3
"""
SmartPark Dashboard - Elastic Beanstalk Version
NCI Fog & Edge Computing Project
"""

from flask import Flask, render_template, jsonify, send_from_directory
import boto3
from decimal import Decimal
from datetime import datetime
import os

# Initialize Flask
application = Flask(__name__, 
    template_folder='.',
    static_folder='.'
)

# AWS Configuration
AWS_REGION = 'us-east-1'
DYNAMODB_TABLE = 'SmartParkData'

# Initialize DynamoDB
try:
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    table = dynamodb.Table(DYNAMODB_TABLE)
    print("✅ DynamoDB connected")
except Exception as e:
    print(f"⚠️ DynamoDB issue: {e}")
    table = None

def convert_decimals(obj):
    """Convert Decimal to float for JSON"""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    return obj

@application.route('/')
def index():
    """Serve dashboard HTML"""
    try:
        return send_from_directory('.', 'index.html')
    except:
        return """
        <h1>SmartPark Dashboard</h1>
        <p>Error: index.html not found</p>
        <p>Files in directory: {}</p>
        """.format(os.listdir('.'))

@application.route('/api/current')
def get_current():
    """Get latest reading"""
    if not table:
        return jsonify({'status': 'error', 'message': 'DynamoDB not connected'}), 500

    try:
        response = table.query(
            KeyConditionExpression='deviceId = :did',
            ExpressionAttributeValues={':did': 'smartpark_fog_node_nyc'},
            ScanIndexForward=False,
            Limit=1
        )

        if response['Items']:
            data = convert_decimals(response['Items'][0])
            return jsonify({'status': 'success', 'data': data})
        return jsonify({'status': 'error', 'message': 'No data. Run fog_node.py first'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@application.route('/api/history')
def get_history():
    """Get last 50 readings"""
    if not table:
        return jsonify({'status': 'error', 'message': 'DynamoDB not connected'}), 500

    try:
        response = table.query(
            KeyConditionExpression='deviceId = :did',
            ExpressionAttributeValues={':did': 'smartpark_fog_node_nyc'},
            ScanIndexForward=False,
            Limit=50
        )

        items = response['Items']
        if not items:
            return jsonify({'status': 'error', 'message': 'No data found'}), 404

        items = [convert_decimals(item) for item in items]

        chart_data = {
            'timestamps': [item['timestamp'] for item in reversed(items)],
            'temperature': [item.get('sensors', {}).get('temperature_c', 0) for item in reversed(items)],
            'humidity': [item.get('sensors', {}).get('humidity_percent', 0) for item in reversed(items)],
            'uv': [item.get('sensors', {}).get('uv_index', 0) for item in reversed(items)],
            'soil': [item.get('sensors', {}).get('soil_moisture_percent', 0) for item in reversed(items)],
            'pm25': [item.get('sensors', {}).get('pm25_ug_m3', 0) for item in reversed(items)],
            'scores': [item.get('parkScore', 0) for item in reversed(items) if 'parkScore' in item]
        }

        return jsonify({'status': 'success', 'data': chart_data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@application.route('/api/alerts')
def get_alerts():
    """Get recent alerts"""
    if not table:
        return jsonify({'status': 'error', 'message': 'DynamoDB not connected'}), 500

    try:
        response = table.scan()
        alerts = []

        for item in response['Items']:
            if item.get('alerts') and len(item['alerts']) > 0:
                alerts.append({
                    'timestamp': item.get('timestamp', ''),
                    'alerts': convert_decimals(item['alerts']),
                    'score': float(item.get('parkScore', 0)) if isinstance(item.get('parkScore'), Decimal) else item.get('parkScore', 0)
                })

        alerts.sort(key=lambda x: x['timestamp'], reverse=True)
        return jsonify({'status': 'success', 'alerts': alerts[:10]})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# For local testing
if __name__ == '__main__':
    application.run(host='0.0.0.0', port=8080, debug=True)