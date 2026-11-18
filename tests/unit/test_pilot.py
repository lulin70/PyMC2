"""驾驶员技能单测（M4）：SkillStats 双计数、命中率派生、rank 边界/封顶、kills 与 Mech 挂载。"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Mech
from pymc2.domain.entities.pilot import MAX_GUNNERY_RANK, Pilot, PilotSkill, SkillStats


class TestSkillStats:
    def test_record_hit_counts_use_and_success(self) -> None:
        pilot = Pilot("gunner")
        pilot.record_shot(True)
        stats = pilot.skill_stats[PilotSkill.GUNNERY]
        assert (stats.uses, stats.successes) == (1, 1)

    def test_record_miss_counts_use_only(self) -> None:
        pilot = Pilot("gunner")
        pilot.record_shot(False)
        pilot.record_shot(False)
        stats = pilot.skill_stats[PilotSkill.GUNNERY]
        assert (stats.uses, stats.successes) == (2, 0)

    def test_default_skill_table_has_gunnery_only(self) -> None:
        stats = Pilot().skill_stats
        assert list(stats) == [PilotSkill.GUNNERY]
        assert stats[PilotSkill.GUNNERY] == SkillStats()


class TestGunneryHitRate:
    def test_no_shots_is_zero(self) -> None:
        assert Pilot("gunner").gunnery_hit_rate == 0.0

    def test_ratio_of_successes_to_uses(self) -> None:
        pilot = Pilot("gunner")
        for hit in (True, False, True, True):
            pilot.record_shot(hit)
        assert pilot.gunnery_hit_rate == 0.75


class TestGunneryRank:
    def test_fresh_pilot_rank_is_zero(self) -> None:
        assert Pilot("gunner").gunnery_rank == 0.0

    def test_full_hit_rate_without_kills_is_two(self) -> None:
        pilot = Pilot("gunner")
        pilot.record_shot(True)
        assert pilot.gunnery_rank == 2.0

    def test_kills_contribute_quarter_each(self) -> None:
        pilot = Pilot("gunner")
        pilot.record_shot(False)  # hit_rate 保持 0
        for _ in range(4):
            pilot.record_kill()
        assert pilot.gunnery_rank == 1.0

    def test_boundary_twelve_kills_full_hit_rate_caps_at_five(self) -> None:
        pilot = Pilot("gunner")
        pilot.record_shot(True)  # hit_rate 1.0 → 2.0 分量
        for _ in range(12):
            pilot.record_kill()  # 3.0 分量
        assert pilot.gunnery_rank == MAX_GUNNERY_RANK == 5.0

    def test_rank_clamped_beyond_cap(self) -> None:
        pilot = Pilot("gunner")
        pilot.record_shot(True)
        for _ in range(30):
            pilot.record_kill()
        assert pilot.gunnery_rank == 5.0

    def test_kill_component_only_raises_rank(self) -> None:
        pilot = Pilot("gunner")
        pilot.record_shot(False)
        assert pilot.gunnery_rank == 0.0
        pilot.record_kill()
        assert pilot.gunnery_rank == 0.25
        pilot.record_kill()
        assert pilot.gunnery_rank == 0.5

    def test_hit_rate_component_grows_with_first_hits(self) -> None:
        pilot = Pilot("gunner")
        pilot.record_shot(False)
        first = pilot.gunnery_rank
        pilot.record_shot(True)
        pilot.record_shot(True)
        assert pilot.gunnery_rank > first


class TestKills:
    def test_record_kill_increments(self) -> None:
        pilot = Pilot("gunner")
        assert pilot.kills == 0
        pilot.record_kill()
        pilot.record_kill()
        assert pilot.kills == 2


class TestMechPilotAttach:
    def test_mech_default_has_no_pilot(self) -> None:
        mech = Mech("m-01", Faction.PLAYER, 0.0, 0.0)
        assert mech.pilot is None

    def test_mech_accepts_pilot(self) -> None:
        pilot = Pilot("gunner")
        mech = Mech("m-01", Faction.PLAYER, 0.0, 0.0, pilot=pilot)
        assert mech.pilot is pilot
