from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('runtime', '0001_initial'),
    ]

    operations = [
        # ContainerInstance: rename backend_port → app_port, drop frontend_port
        migrations.RenameField(
            model_name='containerinstance',
            old_name='backend_port',
            new_name='app_port',
        ),
        migrations.AlterField(
            model_name='containerinstance',
            name='app_port',
            field=models.IntegerField(blank=True, null=True, verbose_name='app port'),
        ),
        migrations.RemoveField(
            model_name='containerinstance',
            name='frontend_port',
        ),
        # PortAllocation: rename backend_port → app_port, drop frontend_port
        migrations.RenameField(
            model_name='portallocation',
            old_name='backend_port',
            new_name='app_port',
        ),
        migrations.AlterField(
            model_name='portallocation',
            name='app_port',
            field=models.IntegerField(unique=True, verbose_name='app port'),
        ),
        migrations.RemoveField(
            model_name='portallocation',
            name='frontend_port',
        ),
    ]
