from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('books_core', '0003_summary_book_alter_processingjob_job_type_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='prompt',
            name='file_path',
            field=models.CharField(
                blank=True,
                help_text='Path to prompt file relative to prompts/ directory',
                max_length=500,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='prompt',
            name='file_checksum',
            field=models.CharField(
                blank=True,
                help_text='MD5 checksum of file content for change detection',
                max_length=64,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='prompt',
            name='last_synced_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When this prompt was last synced from file',
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name='prompt',
            index=models.Index(fields=['file_path'], name='books_core__file_pa_idx'),
        ),
    ]
