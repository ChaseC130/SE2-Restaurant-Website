
## Demo admin login

Email: `admin@example.com`  
Password: `admin123`

You can change these before the first run with environment variables:

```bash
set ADMIN_EMAIL=your@email.com
set ADMIN_PASSWORD=yourpassword
set SECRET_KEY=change-this-secret
```

## How to run

From the project folder:

virtual enviroment is not necessary if it causes problems
```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

Open:

```
http://127.0.0.1:5000
```

## Important note

This does not send real email through Gmail or SMTP by default. For demonstration all emails are written to:

instance/email_outbox.log
```

This includes:

- account confirmation links
- reservation confirmations
- waitlist confirmations
- order confirmations

To confirm a newly registered account, open `instance/email_outbox.log`, copy the confirmation link, and paste it into your browser.

## Reset the database

To reset all demo data:

```bash
flask --app app init-db
```

Or delete:

```text
instance/restaurant.db
```

then run `python app.py` again.

## Main pages

- `/` - Home
- `/menu` - Public menu
- `/order` - Online ordering
- `/reserve` - Reservations
- `/waitlist` - Walk-in waitlist
- `/register` - Create account
- `/login` - Login
- `/account` - Customer account history
- `/admin` - Admin dashboard
- `/admin/menu` - Admin menu editor


## Remaining limitations

- CSRF protection with Flask-WTF
- Real SMTP or transactional email service
- Stronger input validation
- HTTPS
- A stronger database such as PostgreSQL
- Admin password reset flow
