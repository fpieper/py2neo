#!/usr/bin/env python
# -*- encoding: utf-8 -*-

# Copyright 2011-2014, Nigel Small
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from collections import OrderedDict
import logging

from py2neo import Service, Resource, Node, Rel, Relationship, Subgraph, Path, Finished
from py2neo.compat import integer, string, xstr, ustr
from py2neo.cypher.lang import cypher_escape
from py2neo.cypher.error.core import CypherError, CypherTransactionError
from py2neo.packages.tart.tables import TextTable
from py2neo.util import is_collection


__all__ = ["CypherResource", "CypherTransaction", "RecordListList", "RecordList",
           "Record", "RecordProducer"]


log = logging.getLogger("py2neo.cypher")


def presubstitute(statement, parameters):
    more = True
    while more:
        before, opener, key = statement.partition(u"«")
        if opener:
            key, closer, after = key.partition(u"»")
            try:
                value = parameters.pop(key)
            except KeyError:
                raise KeyError("Expected a presubstitution parameter named %r" % key)
            if isinstance(value, integer):
                value = ustr(value)
            elif isinstance(value, tuple) and all(map(lambda x: isinstance(x, integer), value)):
                value = u"%d..%d" % (value[0], value[-1])
            elif is_collection(value):
                value = ":".join(map(cypher_escape, value))
            else:
                value = cypher_escape(value)
            statement = before + value + after
        else:
            more = False
    return statement, parameters


class CypherResource(Service):
    """ Service wrapper for all Cypher functionality, providing access
    to transactions as well as single statement execution and streaming.

    This class will usually be instantiated via a :class:`py2neo.Graph`
    object and will be made available through the
    :attr:`py2neo.Graph.cypher` attribute. Therefore, for single
    statement execution, simply use the :func:`execute` method::

        from py2neo import Graph
        graph = Graph()
        results = graph.cypher.execute("MATCH (n:Person) RETURN n")

    """

    error_class = CypherError

    __instances = {}

    def __new__(cls, transaction_uri):
        try:
            inst = cls.__instances[transaction_uri]
        except KeyError:
            inst = super(CypherResource, cls).__new__(cls)
            inst.bind(transaction_uri)
            cls.__instances[transaction_uri] = inst
        return inst

    def post(self, statement, parameters=None, **kwparameters):
        """ Post a Cypher statement to this resource, optionally with
        parameters.

        :arg statement: A Cypher statement to execute.
        :arg parameters: A dictionary of parameters.
        :arg kwparameters: Extra parameters supplied by keyword.
        """
        tx = CypherTransaction(self.uri)
        tx.append(statement, parameters, **kwparameters)
        results = tx.post(commit=True)
        return results[0]

    def execute(self, statement, parameters=None, **kwparameters):
        """ Execute a single Cypher statement.

        :arg statement: A Cypher statement to execute.
        :arg parameters: A dictionary of parameters.
        :rtype: :class:`py2neo.cypher.RecordList`
        """
        tx = CypherTransaction(self.uri)
        tx.append(statement, parameters, **kwparameters)
        results = tx.commit()
        return results[0]

    def execute_one(self, statement, parameters=None, **kwparameters):
        """ Execute a single Cypher statement and return the value from
        the first column of the first record returned.

        :arg statement: A Cypher statement to execute.
        :arg parameters: A dictionary of parameters.
        :return: Single return value or :const:`None`.
        """
        tx = CypherTransaction(self.uri)
        tx.append(statement, parameters, **kwparameters)
        results = tx.commit()
        try:
            return results[0][0][0]
        except IndexError:
            return None

    def begin(self):
        """ Begin a new transaction.

        :rtype: :class:`py2neo.cypher.CypherTransaction`
        """
        return CypherTransaction(self.uri)


class CypherTransaction(object):
    """ A transaction is a transient resource that allows multiple Cypher
    statements to be executed within a single server transaction.
    """

    error_class = CypherTransactionError

    def __init__(self, uri):
        log.info("begin")
        self.statements = []
        self.__begin = Resource(uri)
        self.__begin_commit = Resource(uri + "/commit")
        self.__execute = None
        self.__commit = None
        self.__finished = False
        self.graph = self.__begin.graph

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.commit()

    def __assert_unfinished(self):
        if self.__finished:
            raise Finished(self)

    @property
    def _id(self):
        """ The internal server ID of this transaction, if available.
        """
        if self.__execute is None:
            return None
        else:
            return int(self.__execute.uri.path.segments[-1])

    @property
    def finished(self):
        """ Indicates whether or not this transaction has been completed or is
        still open.

        :return: :py:const:`True` if this transaction has finished,
                 :py:const:`False` otherwise
        """
        return self.__finished

    def append(self, statement, parameters=None, **kwparameters):
        """ Add a statement to the current queue of statements to be
        executed.

        :arg statement: the statement to append
        :arg parameters: a dictionary of execution parameters
        """
        self.__assert_unfinished()

        s = ustr(statement)
        p = {}

        def add_parameters(params):
            if params:
                for k, v in dict(params).items():
                    if isinstance(v, (Node, Rel, Relationship)):
                        v = v._id
                    p[k] = v

        try:
            add_parameters(statement.parameters)
        except AttributeError:
            pass
        add_parameters(dict(parameters or {}, **kwparameters))

        s, p = presubstitute(s, p)

        # OrderedDict is used here to avoid statement/parameters ordering bug
        log.info("append %r %r", s, p)
        self.statements.append(OrderedDict([
            ("statement", s),
            ("parameters", p),
            ("resultDataContents", ["REST"]),
        ]))

    def post(self, commit=False):
        self.__assert_unfinished()
        if commit:
            log.info("commit")
            resource = self.__commit or self.__begin_commit
            self.__finished = True
        else:
            log.info("process")
            resource = self.__execute or self.__begin
        rs = resource.post({"statements": self.statements})
        location = rs.location
        if location:
            self.__execute = Resource(location)
        j = rs.content
        rs.close()
        self.statements = []
        if "commit" in j:
            self.__commit = Resource(j["commit"])
        if "errors" in j:
            errors = j["errors"]
            if len(errors) >= 1:
                error = errors[0]
                raise self.error_class.hydrate(error)
        results = [{"columns": result["columns"], "data": [data["rest"] for data in result["data"]]}
                   for result in j["results"]]
        log.info("results %r", results)
        return results

    def process(self):
        """ Send all pending statements to the server for execution, leaving
        the transaction open for further statements. Along with
        :meth:`append <.CypherTransaction.append>`, this method can be used to
        batch up a number of individual statements into a single HTTP request::

            from py2neo import Graph

            graph = Graph()
            statement = "MERGE (n:Person {name:{N}}) RETURN n"

            tx = graph.cypher.begin()

            def add_names(*names):
                for name in names:
                    tx.append(statement, {"N": name})
                tx.process()

            add_names("Homer", "Marge", "Bart", "Lisa", "Maggie")
            add_names("Peter", "Lois", "Chris", "Meg", "Stewie")

            tx.commit()

        :return: list of results from pending statements
        """
        return RecordListList.from_results(self.post(), self.graph)

    def commit(self):
        """ Send all pending statements to the server for execution and commit
        the transaction.

        :return: list of results from pending statements
        """
        return RecordListList.from_results(self.post(commit=True), self.graph)

    def rollback(self):
        """ Rollback the current transaction.
        """
        self.__assert_unfinished()
        log.info("rollback")
        try:
            if self.__execute:
                self.__execute.delete()
        finally:
            self.__finished = True


class RecordListList(list):
    """ Container for multiple RecordList instances that presents a more
    consistent representation.
    """

    @classmethod
    def from_results(cls, results, graph):
        return cls(RecordList(graph, result["columns"], result["data"]) for result in results)

    def __repr__(self):
        out = []
        for i in self:
            out.append(repr(i))
        return "\n".join(out)


class RecordList(object):
    """ A list of records returned from the execution of a Cypher statement.
    """

    def __init__(self, graph, columns, records):
        self.graph = graph
        self.producer = RecordProducer(columns)
        self.__records = records

    def __repr__(self):
        out = ""
        if self.columns:
            table = TextTable((None,) + self.columns, border=True)
            for i, record in enumerate(self):
                table.append([i + 1] + list(record))
            out = repr(table)
        return out

    def __len__(self):
        return len(self.__records)

    def __getitem__(self, index):
        if isinstance(self.__records[index], list):
            self.__records[index] = self.producer.produce(self.graph.hydrate(self.__records[index]))
        return self.__records[index]

    def __iter__(self):
        for index, record in enumerate(self.__records):
            if isinstance(record, list):
                record = self.__records[index] = self.producer.produce(self.graph.hydrate(record))
            yield record

    @property
    def columns(self):
        return self.producer.columns

    @property
    def one(self):
        """ The first record from this result, reduced to a single value
        if that record only consists of a single column. If no records
        are available, :const:`None` is returned.
        """
        try:
            record = self[0]
        except IndexError:
            return None
        else:
            if len(record) == 0:
                return None
            elif len(record) == 1:
                return record[0]
            else:
                return record

    def to_subgraph(self):
        """ Convert a RecordList into a Subgraph.
        """
        entities = []
        for record in self:
            for value in record:
                if isinstance(value, (Node, Path)):
                    entities.append(value)
        return Subgraph(*entities)


class Record(object):
    """ A simple object containing values from a single row of a Cypher
    result. Each value can be retrieved by column position or name,
    supplied as either an index key or an attribute name.

    Consider the record below::

           | person                     | name
        ---+----------------------------+-------
         1 | (n1:Person {name:"Alice"}) | Alice

    If this record is named ``r``, the following expressions
    are equivalent and will return the value ``'Alice'``::

        r[1]
        r["name"]
        r.name

    """

    __producer__ = None

    def __init__(self, values):
        self.__values__ = tuple(values)
        columns = self.__producer__.columns
        for i, column in enumerate(columns):
            setattr(self, column, values[i])

    def __repr__(self):
        out = ""
        columns = self.__producer__.columns
        if columns:
            table = TextTable(columns, border=True)
            table.append([getattr(self, column) for column in columns])
            out = repr(table)
        return out

    def __eq__(self, other):
        try:
            return vars(self) == vars(other)
        except TypeError:
            return tuple(self) == tuple(other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __len__(self):
        return len(self.__values__)

    def __iter__(self):
        return iter(self.__values__)

    def __getitem__(self, item):
        if isinstance(item, integer):
            return self.__values__[item]
        elif isinstance(item, string):
            return getattr(self, item)
        else:
            raise LookupError(item)


class RecordProducer(object):

    def __init__(self, columns):
        self.columns = tuple(column for column in columns if not column.startswith("_"))
        dct = dict.fromkeys(self.columns)
        dct["__producer__"] = self
        self.__type = type(xstr("Record"), (Record,), dct)

    def __repr__(self):
        return "RecordProducer(columns=%r)" % (self.columns,)

    def __len__(self):
        return len(self.columns)

    def produce(self, values):
        """ Produce a record from a set of values.
        """
        return self.__type(values)
