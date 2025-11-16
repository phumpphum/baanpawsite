# Shopsite (Django) with Sales Report

Install:
```
python -m venv venv
# Windows: venv\Scripts\activate | macOS/Linux: source venv/bin/activate
pip install django pillow
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Pages:
- /products/
- /products/new/
- /sales/new/
- /sales/history/
- /reports/sales/?g=day  (change to g=month for monthly)
