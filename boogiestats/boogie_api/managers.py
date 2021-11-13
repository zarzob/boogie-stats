from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class ScoreManager(models.Manager):
    def create(self, song, player, score, comment, profile_name):
        new_is_top = False

        try:
            previous_top = song.scores.get(player=player, is_top=True)
        except ObjectDoesNotExist:
            previous_top = None
            new_is_top = True

        if previous_top and previous_top.score < score:
            new_is_top = True
            previous_top.is_top = False
            previous_top.save()

        score_object = self.model(
            song=song,
            player=player,
            score=score,
            comment=comment,
            profile_name=profile_name,
            is_top=new_is_top,
        )

        score_object.save()

        return score_object
