"""
Migration to add PDF support:
- Rename epub_file to source_file
- Add file_type field
- Add index on file_type
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('books_core', '0004_prompt_file_sync_fields'),
    ]

    operations = [
        # Rename epub_file to source_file
        migrations.RenameField(
            model_name='book',
            old_name='epub_file',
            new_name='source_file',
        ),
        # Add file_type field
        migrations.AddField(
            model_name='book',
            name='file_type',
            field=models.CharField(
                choices=[('epub', 'EPUB'), ('pdf', 'PDF')],
                db_index=True,
                default='epub',
                max_length=10,
            ),
        ),
        # Add index for file_type
        migrations.AddIndex(
            model_name='book',
            index=models.Index(fields=['file_type'], name='books_core__file_ty_1c5c9c_idx'),
        ),
    ]
