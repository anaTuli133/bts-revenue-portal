import csv
import io
import calendar

from flask import Flask, jsonify, render_template, request, Response

from db import fetch_revenue_report, fetch_filter_options, fetch_available_months, fetch_dashboard_summary
from config import CATEGORY_COLUMNS

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('revenue.html')


@app.route('/api/months')
def api_months():
    try:
        return jsonify(fetch_available_months())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/filters')
def api_filters():
    try:
        return jsonify(fetch_filter_options())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _parse_report_params(args):
    category = args.get('category', 'GT').upper()
    if category not in CATEGORY_COLUMNS:
        raise ValueError(f'category must be one of {list(CATEGORY_COLUMNS)}')
    month_key = args.get('month')
    if not month_key or len(month_key) != 6 or not month_key.isdigit():
        raise ValueError('month must be provided as YYYYMM, e.g. 202607')
    division = args.get('division') or None
    district = args.get('district') or None
    upazila = args.get('upazila') or None
    return category, month_key, division, district, upazila


def _filename_slug(value):
    """Sanitizes a Division/District/Upazila name for safe use inside a CSV filename."""
    cleaned = ''.join(ch if (ch.isalnum() or ch in ('-', '_')) else '-' for ch in str(value).strip())
    while '--' in cleaned:
        cleaned = cleaned.replace('--', '-')
    return cleaned.strip('-')


@app.route('/api/revenue-report')
def api_revenue_report():
    """category, month (YYYYMM), division, district, upazila"""
    try:
        category, month_key, division, district, upazila = _parse_report_params(request.args)
        rows = fetch_revenue_report(category, month_key, division, district, upazila)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    year, month = int(month_key[:4]), int(month_key[4:6])
    return jsonify({
        'category': category,
        'month_key': month_key,
        'days_in_month': calendar.monthrange(year, month)[1],
        'rows': rows
    })


@app.route('/api/revenue-report/csv')
def api_revenue_report_csv():
    try:
        category, month_key, division, district, upazila = _parse_report_params(request.args)
        rows = fetch_revenue_report(category, month_key, division, district, upazila)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    year, month = int(month_key[:4]), int(month_key[4:6])
    days_in_month = calendar.monthrange(year, month)[1]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Division', 'District', 'Upazila', 'Site ID'] + [str(d) for d in range(1, days_in_month + 1)])
    for row in rows:
        writer.writerow(
            [row.get('DIVISION', ''), row.get('DISTRICT', ''), row.get('UPAZILA', ''), row.get('SITE_ID', '')]
            + [row.get(f'D{d}', 0) for d in range(1, days_in_month + 1)]
        )

    # Filename reflects whichever Division/District/Upazila is currently
    # filtered on, so a downloaded file is identifiable without opening it.
    name_parts = [f"BTS_Revenue_{category}_{month_key}"]
    for part in (division, district, upazila):
        if part:
            slug = _filename_slug(part)
            if slug:
                name_parts.append(slug)
    filename = "_".join(name_parts) + ".csv"

    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@app.route('/api/dashboard-summary')
def api_dashboard_summary():
    """month (YYYYMM), division, district, upazila"""
    month_key = request.args.get('month')
    if not month_key or len(month_key) != 6 or not month_key.isdigit():
        return jsonify({'error': 'month must be provided as YYYYMM, e.g. 202607'}), 400
    division = request.args.get('division') or None
    district = request.args.get('district') or None
    upazila = request.args.get('upazila') or None

    try:
        data = fetch_dashboard_summary(month_key, division, district, upazila)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    data['month_key'] = month_key
    return jsonify(data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)