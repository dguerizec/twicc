"""Make ModelPrice.provider non-nullable and rebuild the unique key around it.

The pre-existing ``unique_together = (model_id, effective_date)`` is
replaced by a ``UniqueConstraint`` that includes ``provider`` so two
providers can publish prices for the same ``model_id`` without
colliding. The legacy ``(model_id, -effective_date)`` index is dropped
without a replacement: the new unique constraint's implicit index
covers every read pattern we have (``WHERE provider=? AND model_id=?``
with optional ``ORDER BY -effective_date LIMIT 1``) via a B-tree
reverse scan, and the table is too small for a redundant DESC index to
matter.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0081_modelprice_provider_nullable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="modelprice",
            name="provider",
            field=models.CharField(max_length=50),
        ),
        migrations.AlterUniqueTogether(
            name="modelprice",
            unique_together=set(),
        ),
        migrations.RemoveIndex(
            model_name="modelprice",
            name="core_modelp_model_i_8128b9_idx",
        ),
        migrations.AddConstraint(
            model_name="modelprice",
            constraint=models.UniqueConstraint(
                fields=["provider", "model_id", "effective_date"],
                name="uniq_mp_prov_mid_date",
            ),
        ),
    ]
