import datetime
from collections import defaultdict
from django.utils.dateparse import parse_datetime, parse_date

from .case_utils import get_adherence_cases_between_dates_from_episode
from corehq.apps.fixtures.models import FixtureDataItem
from corehq.util.soft_assert import soft_assert


DOSE_TAKEN_INDICATORS = [
    'directly_observed_dose',
    'unobserved_dose',
    'self_administered_dose',
]
DAILY_SCHEDULE_FIXTURE_NAME = 'adherence_schedules'
SCHEDULE_ID_FIXTURE = 'id'
CASE_TYPE_EPISODE = 'episode'


@periodic_task(
    run_every=crontab(minute=1, hour="*/6"),
    queue=getattr(settings, 'CELERY_PERIODIC_QUEUE', 'celery')
)
def enikshay_adherence_task():
    # get toggle enabled domains
    domains = []
    for domain in domains:
        updater = PeriodicCaseUpdater(domain)
        updater.run()


class PeriodicCaseUpdater(object):

    def __init__(self, domain):
        self.domain = domain
        self.purge_date = datetime.datetime.today() - datetime.timedelta(days=60)

    def run(self):
        for episode in self._get_open_episode_cases():
            update = EpisodeUpdate(episode, self)
            if update.update_kwargs():
                submit_case_blocks(
                    [ElementTree.tostring(CaseBlock(**update.update_kwargs()).as_xml())],
                    self.domain
                )

    def _get_open_episode_cases():
        case_accessor = CaseAccessors(self.domain)
        case_ids = get_open_case_ids_in_domain_by_type(CASE_TYPE_EPISODE)
        return case_accessor.iter_cases(case_ids)

    @memoized
    def get_doses_data():
        # return 'doses_per_week' by 'schedule_id' from the Fixture data
        fixtures = FixtureDataItem.get_indexed_items(self.domain, DAILY_SCHEDULE_FIXTURE_NAME, SCHEDULE_ID_FIXTURE)
        return dict((k, int(fixture['doses_per_week'])) for k, fixture in fixtures.items())


def index_by_adherence_date(adherence_cases):
    """
    inde
    """
    by_date = defaultdict(list)
    for case in adherence_cases:
        adherance_date = parse_date(case.dynamic_case_properties().get('adherence_date'))
        by_date[adherance_date].append(case)
    return by_date


class EpisodeUpdate(object):

    def __init__(self, episode_case, case_updater):
        self.episode = episode_case
        self.case_updater = case_updater

    def get_property(self, property):
        return self.episode.dynamic_case_properties().get(property)

    def get_latest_adherence_case_for_episode(self):
        """
        return open case of type 'adherence' reversed-indexed to episode and
            with latest 'adherence_date' property
        """
        case_accessor = CaseAccessors(self.case_updater.domain)
        indexed_cases = case_accessor.get_reverse_indexed_cases([self.episode.case_id])
        latest_date = 0
        latest_case = None
        for case in indexed_cases:
            adherence_date = parse_datetime(case.dynamic_case_properties().get('adherence_date'))
            if (not case.closed and
               case.episode.type == CASE_TYPE_ADHERENCE and
               adherence_date > latest_date):
                latest_date = adherence_date
                latest_case = case
        return latest_case

    def update_json():
        adherence_schedule_date_start = parse_datetime(self.get_property('adherence_schedule_date_start'))
        if not adherence_schedule_date_start:
            # adherence schedule hasn't been selected, so no update necessary
            return {}

        if adherence_schedule_date_start > self.case_updater.purge_date:
            return {
                'aggregated_score_date_calculated': adherence_schedule_date_start - 1
                'expected_doses_taken': 0
                'aggregated_score_count_taken': 0
            }
        else:
            update = {}
            adherence_case = self.get_latest_adherence_case_for_episode()
            adherence_date = parse_datetime(adherence_case.dynamic_case_properties().get('adherence_date'))
            if not adherence_case:
                update["aggregated_score_date_calculated"] = self.case_updater.purge_date
                update["aggregated_score_count_taken"] = 0
            elif adherence_date < self.case_updater.purge_date:
                update["aggregated_score_date_calculated"] = adherence_date
            else:
                update["aggregated_score_date_calculated"] = self.case_updater.purge_date

            # calculate 'aggregated_score_count_taken'
            if adherence_case:
                adherence_cases = get_adherence_cases_between_dates_from_episode(
                    self.case_updater.domain
                    self.episode,
                    adherence_schedule_date_start,
                    update["aggregated_score_date_calculated"]
                )
                adherence_cases_by_date = index_by_adherence_date(adherence_cases)
                is_dose_taken_by_date = {}
                for date, cases in adherence_cases_by_date.iteritems():
                    is_dose_taken_by_date[date] = any([
                        case.dynamic_case_properties().get('adherence_value') in DOSE_TAKEN_INDICATORS
                        for case in cases
                    ])
                total_taken_count = is_dose_taken_by_date.values().count(True)
                update["aggregated_score_count_taken"] = total_taken_count

            # calculate 'expected_doses_taken' score
            dose_data = self.case_updater.get_doses_data()
            adherence_schedule_id = self.get_property('adherence_schedule_id') or DAILY_SCHEDULE_ID
            doses_per_week = dose_data.get(adherence_schedule_id)
            if doses_per_week:
                update['expected_doses_taken'] = ((
                    aggregated_score_date_calculated - adherence_schedule_date_start) / 7
                ) * doses_per_week
            else:
                update['expected_doses_taken'] = 0
                soft_assert(notify_admins=True)(
                    True,
                    "No fixture item found with schedule_id {}".format(adherence_schedule_id)
                )
            return update