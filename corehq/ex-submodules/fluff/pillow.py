import logging

from couchdbkit import ResourceNotFound

from corehq.apps.change_feed.consumer.feed import KafkaChangeFeed
from dimagi.utils.read_only import ReadOnlyObject
from pillowtop.checkpoints.manager import PillowCheckpoint, \
    PillowCheckpointEventHandler
from pillowtop.checkpoints.util import get_machine_id
from pillowtop.pillow.interface import ConstructedPillow
from pillowtop.processors.interface import PillowProcessor
from .signals import BACKEND_SQL, BACKEND_COUCH, indicator_document_updated


class FluffPillowProcessor(PillowProcessor):

    def __init__(self, indicator_class, delete_filtered=False):
        self.indicator_class = indicator_class
        self.domains = indicator_class.domains
        self.document_filter = indicator_class.document_filter
        self.document_class = indicator_class.document_class
        self.save_direct_to_sql = indicator_class().save_direct_to_sql
        self.deleted_types = indicator_class.deleted_types
        self.doc_type = indicator_class.document_class._doc_type
        self.wrapper = indicator_class.wrapper or indicator_class.document_class
        self.delete_filtered = delete_filtered

        self._assert_valid()

    @classmethod
    def get_sql_engine(cls):
        engine = getattr(cls, '_engine', None)
        if not engine:
            import sqlalchemy
            from django.conf import settings
            engine = sqlalchemy.create_engine(settings.SQL_REPORTING_DATABASE_URL)
            cls._engine = engine
        return engine

    def _assert_valid(self):
        assert self.domains
        assert None not in self.domains
        assert self.doc_type is not None
        assert self.doc_type not in self.deleted_types

    def process_change(self, pillow_instance, change, is_retry_attempt=False):
        if self.should_process_change(change):
            try:
                doc_dict = self.change_transform(change.get_document())
                if doc_dict:
                    self.change_transport(doc_dict)
            except Exception:
                logging.exception('something went wrong processing change %s (%s)' %
                                  (change.get('seq', None), change['id']))

    def should_process_change(self, change):
        def domain_filter(domain):
            return domain in self.domains

        def doc_type_filter(doc_type):
            return self._is_doc_type_match(doc_type) or self._is_doc_type_deleted_match(doc_type)

        def _get_document_or_dict(change):
            return change.get_document() or {}

        # if metadata.domain is specified this should never have to get the document out of the DB
        domain = change.metadata.domain if change.metadata else _get_document_or_dict(change).get('domain')
        if domain_filter(domain):
            # same for metadata.document_type
            doc_type = (change.metadata and change.metadata.document_type) or _get_document_or_dict(change).get(
                'doc_type')
            return doc_type_filter(doc_type)

    def _is_doc_type_match(self, type):
        return type == self.doc_type

    def _is_doc_type_deleted_match(self, type):
        return type in self.deleted_types

    def change_transform(self, doc_dict):
        delete = False
        doc = self.wrapper.wrap(doc_dict)
        doc = ReadOnlyObject(doc)

        if self.document_filter and not self.document_filter.filter(doc):
            if self.delete_filtered:
                delete = True
            else:
                return None

        indicator = _get_indicator_doc_from_class_and_id(self.indicator_class, doc.get_id)
        if not self._is_doc_type_deleted_match(doc.doc_type):
            indicator.calculate(doc)
        else:
            indicator['id'] = doc.get_id
            delete = True

        return {
            'doc_dict': doc_dict,
            'indicators': indicator,
            'delete': delete
        }

    def change_transport(self, data):
        indicators = data['indicators']
        diff = indicators.diff(None)  # pass in None for old_doc to force diff with ALL indicators
        if self.save_direct_to_sql:
            engine = self.get_sql_engine()
            if not data['delete']:
                indicators.save_to_sql(diff, engine)
            else:
                indicators.delete_from_sql(engine)
            engine.dispose()
        else:
            if not data['delete']:
                indicators.save()
            else:
                indicators.delete()

        backend = BACKEND_SQL if self.save_direct_to_sql else BACKEND_COUCH
        indicator_document_updated.send(
            sender=self,
            doc=data['doc_dict'],
            diff=diff,
            backend=backend
        )


def _get_indicator_doc_from_class_and_id(indicator_class, doc_id):
    indicator_id = '%s-%s' % (indicator_class.__name__, doc_id)
    try:
        return indicator_class.get(indicator_id)
    except ResourceNotFound:
        return indicator_class(_id=indicator_id)


class FluffPillow(ConstructedPillow):
    def __init__(self, indicator_class, processor):
        self.indicator_class = indicator_class
        self.kafka_topic = indicator_class().kafka_topic
        self.domains = processor.domains
        self.doc_type = processor.doc_type

        name = '{}Pillow'.format(indicator_class.__name__)
        checkpoint = PillowCheckpoint('fluff.{}.{}'.format(name, get_machine_id()))

        super(FluffPillow, self).__init__(
            name=name,
            checkpoint=checkpoint,
            change_feed=KafkaChangeFeed(topics=[self.kafka_topic], group_id=indicator_class.__name__),
            processor=processor,
            change_processed_event_handler=PillowCheckpointEventHandler(
                checkpoint=checkpoint, checkpoint_frequency=1000,
            )
        )


def get_fluff_pillow(indicator_class, delete_filtered=False):
    processor = FluffPillowProcessor(indicator_class, delete_filtered=delete_filtered)

    return FluffPillow(
        indicator_class=indicator_class,
        processor=processor,
    )


def get_fluff_pillow_configs():
    from pillowtop import get_all_pillow_configs
    return [
        config for config in get_all_pillow_configs()
        if config.section == 'fluff'
    ]
