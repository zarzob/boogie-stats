import json
from hashlib import sha256
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import m2m_changed
from django.utils.timezone import now
from django.utils.functional import cached_property

from boogiestats.boogie_api.managers import ScoreManager, PlayerManager

MAX_LEADERBOARD_RIVALS = 3
MAX_LEADERBOARD_ENTRIES = 50


def make_leaderboard_entry(rank, score, is_rival=False, is_self=False):
    return {
        "rank": rank,
        "name": score.player.name or score.player.machine_tag,  # use name if available
        "score": score.score,
        "date": score.submission_date.strftime("%Y-%m-%d %H:%M:%S"),
        "isSelf": is_self,
        "isRival": is_rival,
        "isFail": False,
        "machineTag": score.player.machine_tag,
    }


class Song(models.Model):
    hash = models.CharField(max_length=16, primary_key=True, db_index=True)  # V3 GrooveStats hash 16 a-f0-9
    gs_ranked = models.BooleanField(default=False)
    highscore = models.ForeignKey(
        "Score", null=True, blank=True, on_delete=models.deletion.SET_NULL, related_name="highscore_for"
    )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def get_leaderboard(self, num_entries, player=None):
        num_entries = min(MAX_LEADERBOARD_ENTRIES, num_entries)

        scores = []
        used_score_pks = []

        if player:
            rank, score = self.get_highscore(player)
            if rank:
                scores.append(make_leaderboard_entry(rank, score, is_self=True))
                used_score_pks.append(score.pk)

            for rank, score in self.get_rival_highscores(player):
                scores.append(make_leaderboard_entry(rank, score, is_rival=True))
                used_score_pks.append(score.pk)

        remaining_scores = max(0, num_entries - len(scores))

        top_scores = (
            self.scores.filter(is_top=True)
            .exclude(pk__in=used_score_pks)
            .order_by("-score", "-submission_date")[:remaining_scores]
        )

        for score in top_scores:
            rank = Score.rank(score)
            scores.append(make_leaderboard_entry(rank, score))

        return sorted(scores, key=lambda x: x["score"], reverse=True)

    def get_highscore(self, player) -> (int, "Score"):
        try:
            highscore = self.scores.get(player=player, is_top=True)
        except Score.DoesNotExist:
            return None, None

        return Score.rank(highscore), highscore

    def get_rival_highscores(self, player) -> [(int, "Score")]:
        scores = (
            self.scores.filter(is_top=True, player__in=player.rivals.all())
            .order_by("-score", "-submission_date")[:MAX_LEADERBOARD_RIVALS]
            .all()
        )

        return [(Score.rank(score), score) for score in scores]

    @cached_property
    def chart_info(self):
        """Chart info based on an external (optional) chart database"""
        if settings.BS_CHART_DB_PATH is not None:
            path = Path(settings.BS_CHART_DB_PATH) / self.hash[:2] / f"{self.hash[2:]}.json"
            if path.exists():
                return json.loads(path.read_bytes().decode("utf8", errors="replace"))  # some charts have weird bytes
        return None

    @property
    def display_name(self):
        final_name = self.hash

        if info := self.chart_info:
            artist = info["artisttranslit"] or info["artist"]
            title = info["titletranslit"] or info["title"]

            subtitle = info["subtitletranslit"] or info["subtitle"]
            if subtitle:
                if not (subtitle.startswith("(") and subtitle.endswith(")")):  # fix inconsistent braces
                    subtitle = f"({subtitle})"
                subtitle = f" {subtitle}"

            base_display_name = f"{artist} - {title}{subtitle}"
            final_name = base_display_name

            steps_type = info["steps_type"]
            if steps_type != "dance-single":  # don't display dance-single because it's most common chart type
                final_name += f" ({steps_type})"

        return final_name


class Player(models.Model):
    objects = PlayerManager()

    user = models.OneToOneField(User, null=True, on_delete=models.CASCADE)  # to utilize standard auth stuff
    api_key = models.CharField(max_length=64, db_index=True, unique=True)
    machine_tag = models.CharField(max_length=4)
    name = models.CharField(max_length=64, blank=True, null=True)
    rivals = models.ManyToManyField(
        "self", symmetrical=False, blank=True, help_text="Hold ctrl to select/unselect multiple"
    )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @staticmethod
    def get_by_gs_api_key(gs_api_key):
        api_key = Player.gs_api_key_to_bs_api_key(gs_api_key)
        return Player.objects.filter(api_key=api_key).first()

    @staticmethod
    def gs_api_key_to_bs_api_key(gs_api_key):
        return sha256(gs_api_key[:32].encode("ascii")).hexdigest()

    def __str__(self):
        return f"{self.id} - {self.name} ({self.machine_tag})"


def validate_rivals(sender, **kwargs):
    if kwargs["instance"].rivals.filter(api_key=kwargs["instance"].api_key).count() == 1:
        raise ValidationError("You can't be your own rival")


m2m_changed.connect(validate_rivals, sender=Player.rivals.through)


class Score(models.Model):
    objects = ScoreManager()

    song = models.ForeignKey(Song, on_delete=models.CASCADE, related_name="scores")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="scores")
    submission_date = models.DateTimeField(default=now)
    score = models.IntegerField()
    comment = models.CharField(max_length=200)
    profile_name = models.CharField(max_length=50, blank=True, null=True)
    is_top = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def rank(cls, score):
        return cls.objects.filter(song=score.song, is_top=True, score__gt=score.score).count() + 1
