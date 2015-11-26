import os

from django.db import migrations

SQL_FOLDER = os.path.join('corehq', 'form_processor', 'sql_functions')


def get_sql_from_file(file):
    with open(os.path.join(SQL_FOLDER, file)) as f:
        return f.read()


def migrate_sql_function(name):
    forward = get_sql_from_file('{}_create.sql'.format(name))
    reverse = get_sql_from_file('{}_drop.sql'.format(name))
    return migrations.RunSQL(
        forward,
        reverse
    )
