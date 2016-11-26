import logging
import os

import arrow
from sqlalchemy import Column, Integer, String, DateTime, UnicodeText, Boolean, or_, ForeignKey
from sqlalchemy.orm import relationship, backref
from cifsdk.constants import PYVERSION
from pprint import pprint
from cif.store.sqlite import Base

from cif.constants import TOKEN_CACHE_DELAY

logger = logging.getLogger(__name__)

VALID_FILTERS = ['indicator', 'confidence', 'provider', 'itype', 'group', 'tags']

if PYVERSION > 2:
    basestring = (str, bytes)


class Token(Base):
    __tablename__ = 'tokens'

    id = Column(Integer, primary_key=True)
    username = Column(UnicodeText)
    token = Column(String)
    expires = Column(DateTime)
    read = Column(Boolean)
    write = Column(Boolean)
    revoked = Column(Boolean)
    acl = Column(UnicodeText)
    admin = Column(Boolean)
    last_activity_at = Column(DateTime)

    groups = relationship(
        'Group',
        primaryjoin='and_(Token.id==Group.token_id)',
        backref=backref('groups', uselist=True),
        lazy='subquery',
        cascade="all,delete"
    )
    # group = relationship(Group)


class Group(Base):
    __tablename__ = 'groups'
    id = Column(Integer, primary_key=True)
    group = Column(UnicodeText, index=True)

    token_id = Column(Integer, ForeignKey('tokens.id', ondelete='CASCADE'))
    token = relationship(Token)


class TokenMixin(object):
    def token_admin(self, token):
        x = self.handle().query(Token) \
            .filter_by(token=str(token)) \
            .filter_by(admin=True) \
            .filter(Token.revoked is not True)

        if x.count():
            return True

    def tokens_create(self, data):
        s = self.handle()

        acl = data.get('acl')
        if type(acl) == list:
            acl = ','.join(acl)

        if data.get('expires'):
            data['expires'] = arrow.get(data['expires']).datetime

        t = Token(
            username=data.get('username'),
            token=self._token_generate(),
            acl=acl,
            read=data.get('read'),
            write=data.get('write'),
            expires=data.get('expires'),
            admin=data.get('admin')
        )
        g = Group(
            group=data.get('group', 'everyone'),
            token=t
        )

        s.add(t)
        s.add(g)
        s.commit()
        return self._as_dict(t)

    def tokens_admin_exists(self):
        rv = self.handle().query(Token).filter_by(admin=True)
        if rv.count():
            return rv.first().token

    def tokens_search(self, data):
        rv = self.handle().query(Token)
        if data.get('token'):
            rv = rv.filter_by(token=data['token'])

        if data.get('username'):
            rv = rv.filter_by(username=data['username'])

        if rv.count():
            return [self._as_dict(x) for x in rv]

        return []

    # http://stackoverflow.com/questions/1484235/replace-delete-field-using-sqlalchemy
    def tokens_delete(self, data):
        s = self.handle()

        rv = s.query(Token)
        if data.get('username'):
            rv = rv.filter_by(username=data['username'])
        if data.get('token'):
            rv = rv.filter_by(token=data['token'])

        if rv.count():
            c = rv.count()
            rv.delete()
            s.commit()
            return c
        else:
            return 0

    def token_read(self, token):
        if arrow.utcnow().timestamp > self.token_cache_check:
            self.token_cache = {}
            self.token_cache_check = arrow.utcnow().timestamp + TOKEN_CACHE_DELAY

        if token in self.token_cache:
            try:
                if self.token_cache[token]['read'] is True:
                    return self.token_cache[token]
            except KeyError:
                pass

        t = self.handle().query(Token) \
            .filter_by(token=token) \
            .filter_by(read=True) \
            .filter(Token.revoked is not True) \
            .filter(or_(Token.expires == None, Token.expires > arrow.utcnow().datetime))

        if t.count():
            if not self.token_cache.get('token'):
                self.token_cache[token] = self._as_dict(t.first())
                self.token_cache[token]['groups'] = []
                for g in t.first().groups:
                    self.token_cache[token]['groups'].append(g.group)

            return self.token_cache[token]

    def token_write(self, token):
        if arrow.utcnow().timestamp > self.token_cache_check:
            self.token_cache = {}
            self.token_cache_check = arrow.utcnow().timestamp + TOKEN_CACHE_DELAY

        if token in self.token_cache:
            try:
                if self.token_cache[token]['write'] is True:
                    return self.token_cache[token]
            except KeyError:
                pass

        self.logger.debug('testing token: {}'.format(token))
        t = self.handle().query(Token) \
            .filter_by(token=token) \
            .filter_by(write=True) \
            .filter(Token.revoked is not True) \
            .filter(or_(Token.expires == None, Token.expires > arrow.utcnow().datetime))

        if t.count():
            if not self.token_cache.get('token'):
                self.token_cache[token] = self._as_dict(t.first())
                self.token_cache[token]['groups'] = []
                for g in t.first().groups:
                    self.token_cache[token]['groups'].append(g.group)

            return self.token_cache[token]

    def token_edit(self, data):
        if not data.get('token'):
            return 'token required for updating'

        s = self.handle()
        rv = s.query(Token).filter_by(token=data['token'])

        if not rv.count():
            return 'token not found'

        rv = rv.first()

        if data.get('groups'):
            rv.groups = ','.join(data['groups'])

        s.commit()

        return True

    def token_last_activity_at(self, token, timestamp=None):
        s = self.handle()
        timestamp = arrow.get(timestamp)
        token = token.decode('utf-8')
        if timestamp:
            if arrow.utcnow().timestamp > self.token_cache_check:
                self.token_cache = {}
                self.token_cache_check = arrow.utcnow().timestamp + TOKEN_CACHE_DELAY

            if token in self.token_cache:
                try:
                    if self.token_cache[token]['last_activity_at']:
                        return self.token_cache[token]['last_activity_at']
                except KeyError:
                    x = s.query(Token).filter_by(token=token).update({Token.last_activity_at: timestamp.datetime})
                    s.commit()

                    if x:
                        self.token_cache[token]['last_activity_at'] = timestamp.datetime
                        return timestamp.datetime
                    else:
                        self.logger.error('failed to update token: {}'.format(token))

        else:
            x = s.query(Token).filter_by(token=token)
            if x.count():
                return x.first().last_activity_at
