import pytest

from cortex import config
from cortex.routines.schedule import render_crontab, validate_schedule, write_crontab


@pytest.mark.parametrize("expr,ok", [("30 4 * * *", True), ("*/15 * * * 1-5", True),
                                     ("0 5 * *", False), ("@daily", False), ("0 5 * * * rm", False),
                                     ("0,30 9-17/2 1 1,6 0", True),
                                     ("60 4 * * *", False), ("0 24 * * *", False), ("0 4 0 * *", False),
                                     ("0 4 * 13 *", False), ("0 4 * * 8", False), ("*/0 * * * *", False),
                                     # medidos contra supercronic v0.2.33: el paso no pasa del máximo del
                                     # campo, y el 7 (domingo) vale solo, no como fin de un rango
                                     ("*/59 * * * *", True), ("*/60 * * * *", False), ("0 */24 * * *", False),
                                     ("0 0 */32 * *", False), ("0 0 * */13 *", False), ("0 0 * * */7", False),
                                     ("0 4 * * 7", True), ("0 4 * * 5-7", False),
                                     ("5-1 * * * *", False), ("1- * * * *", False), ("1,,2 * * * *", False)])
def test_validate(expr, ok):
    assert validate_schedule(expr) is ok


def test_crontab_lists_enabled_routines(data):
    cfg = config.load()
    cfg["routines"]["clean"]["enabled"] = False
    text = render_crontab(cfg)
    assert "30 4 * * * /usr/local/bin/cortex-routine consolidate" in text
    assert "cortex-routine snapshot" in text and "clean" not in text


def test_write_is_stable(data):
    path = write_crontab(config.load())
    mtime = path.stat().st_mtime_ns
    write_crontab(config.load())
    assert path.stat().st_mtime_ns == mtime


def test_invalid_saved_schedule_is_left_out_and_logged(data, capsys):
    # Un horario guardado con el validador viejo no tumba supercronic, pero tampoco se pierde en silencio.
    cfg = config.load()
    cfg["routines"]["snapshot"]["schedule"] = "*/60 * * * *"
    text = render_crontab(cfg)
    assert "cortex-routine snapshot" not in text
    assert "# snapshot: horario inválido '*/60 * * * *'" in text
    assert "routine snapshot not scheduled: invalid schedule '*/60 * * * *'" in capsys.readouterr().err
    cfg["routines"]["snapshot"]["enabled"] = False
    assert "snapshot" not in render_crontab(cfg)
