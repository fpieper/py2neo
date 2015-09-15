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


from io import StringIO

from py2neo.compat import ustr, xstr
from py2neo.core import Node, LabelSet, PropertySet, Relationship
from py2neo.cypher.lang import CypherParameter, CypherWriter


__all__ = ["CypherTask", "CreateNode", "MergeNode", "CreateRelationship", "CreateTransaction"]


class CypherTask(object):
    """ The `CypherTask` class can either be used directly or as
    a base class for more specific statement implementations.
    """

    def __init__(self, statement="", parameters=None, **kwparameters):
        self.__statement = statement
        self.__parameters = dict(parameters or {}, **kwparameters)

    def __repr__(self):
        return "<CypherTask statement=%r parameters=%r>" % (self.statement, self.parameters)

    def __str__(self):
        return xstr(self.statement)

    def __unicode__(self):
        return ustr(self.statement)

    @property
    def statement(self):
        """ The Cypher statement.
        """
        return self.__statement

    @property
    def parameters(self):
        """ Dictionary of parameters.
        """
        return self.__parameters


class CreateNode(CypherTask):
    """ :class:`.CypherTask` for creating nodes.
    """

    def __init__(self, *labels, **properties):
        CypherTask.__init__(self)
        self.__node = Node(*labels, **properties)
        self.__return = False
        self.cypher_name = "a"
        self.cypher_parameter = "A"

    @property
    def labels(self):
        """ The full set of labels to apply to the created node.

        :rtype: :class:`py2neo.LabelSet`
        """
        return self.__node.labels

    @property
    def properties(self):
        """ The full set of properties to apply to the created node.

        :rtype: :class:`py2neo.PropertySet`
        """
        return self.__node.properties

    def set(self, *labels, **properties):
        """ Extra labels and properties to apply to the node.
        """
        self.__node.labels.update(labels)
        self.__node.properties.update(properties)
        return self

    def with_return(self):
        """ Include a RETURN clause in the statement.
        """
        self.__return = True
        return self

    @property
    def statement(self):
        """ The full Cypher statement.
        """
        string = StringIO()
        writer = CypherWriter(string)
        writer.write_literal("CREATE ")
        writer.write_node(self.__node, self.cypher_name,
                          CypherParameter(self.cypher_parameter)
                          if self.__node.properties else None)
        if self.__return:
            writer.write_literal(" RETURN ")
            writer.write_literal(self.cypher_name)
        return string.getvalue()

    @property
    def parameters(self):
        """ Dictionary of parameters.
        """
        if self.__node.properties:
            return {self.cypher_parameter: self.properties}
        else:
            return {}


class CreateRelationship(CypherTask):
    """ :class:`.CypherTask` for creating relationships.
    """

    def __init__(self, *triple, **properties):
        CypherTask.__init__(self)
        self.__relationship = Relationship(*triple, **properties)
        self.__return = False

    @property
    def start_node(self):
        """ The start node of the newly created relationship.

        :rtype: :class:`py2neo.Node`
        """
        return self.__relationship.start_node

    @property
    def end_node(self):
        """ The end node of the newly created relationship.

        :rtype: :class:`py2neo.Node`
        """
        return self.__relationship.end_node

    @property
    def type(self):
        """ The type of the newly created relationship.

        :rtype: str
        """
        return self.__relationship.type

    @property
    def properties(self):
        """ The full set of properties to apply to the created relationship.

        :rtype: :class:`py2neo.PropertySet`
        """
        return self.__relationship.properties

    def set(self, **properties):
        """ Extra properties to apply to the node.
        """
        self.__relationship.properties.update(properties)
        return self

    def with_return(self):
        """ Include a RETURN clause in the statement.
        """
        self.__return = True
        return self

    @property
    def statement(self):
        """ The full Cypher statement.
        """
        string = StringIO()
        writer = CypherWriter(string)
        if self.start_node.bound:
            writer.write_literal("MATCH (a) WHERE id(a)={A} ")
        else:
            creator = CreateNode(*self.start_node.labels, **self.start_node.properties)
            creator.cypher_name = "a"
            creator.cypher_parameter = "A"
            writer.write_literal(creator.statement)
            writer.write_literal(" ")
        if self.end_node.bound:
            writer.write_literal("MATCH (b) WHERE id(b)={B} ")
        else:
            creator = CreateNode(*self.start_node.labels, **self.start_node.properties)
            creator.cypher_name = "b"
            creator.cypher_parameter = "B"
            writer.write_literal(creator.statement)
            writer.write_literal(" ")
        writer.write_literal("CREATE ")
        writer.write_literal("(a)")
        writer.write_rel(self.__relationship.rel, "r",
                         CypherParameter("R") if self.__relationship.properties else None)
        writer.write_literal("(b)")
        if self.__return:
            writer.write_literal(" RETURN r")
        return string.getvalue()

    @property
    def parameters(self):
        """ Dictionary of parameters.
        """
        value = {}
        if self.start_node.bound:
            value["A"] = self.start_node._id
        else:
            value["A"] = self.start_node.properties
        if self.end_node.bound:
            value["B"] = self.end_node._id
        else:
            value["B"] = self.end_node.properties
        if self.__relationship.properties:
            value["R"] = self.properties
        return value


class MergeNode(CypherTask):
    """ :class:`.CypherTask` for `merging <http://neo4j.com/docs/stable/query-merge.html>`_
    nodes.

    ::

        >>> from py2neo import Graph
        >>> graph = Graph()
        >>> tx = graph.cypher.begin()
        >>> tx.append(MergeNode("Person", "name", "Alice"))
        >>> tx.commit()
           | a
        ---+-----------------------
         1 | (n170 {name:"Alice"})


    """

    def __init__(self, primary_label, primary_key=None, primary_value=None):
        CypherTask.__init__(self)
        self.__node = Node(primary_label)
        if primary_key is not None:
            self.__node.properties[primary_key] = CypherParameter("A1", primary_value)
        self.__labels = LabelSet()
        self.__properties = PropertySet()
        self.__return = False

    @property
    def labels(self):
        """ The full set of labels to apply to the merged node.

        :rtype: :class:`py2neo.LabelSet`
        """
        l = LabelSet(self.__labels)
        l.update(self.__node.labels)
        return l

    @property
    def properties(self):
        """ The full set of properties to apply to the merged node.

        :rtype: :class:`py2neo.PropertySet`
        """
        p = PropertySet(self.__properties)
        if self.primary_key:
            p[self.primary_key] = self.primary_value
        return p

    @property
    def primary_label(self):
        """ The label on which to merge.
        """
        return list(self.__node.labels)[0]

    @property
    def primary_key(self):
        """ The property key on which to merge.
        """
        try:
            return list(self.__node.properties.keys())[0]
        except IndexError:
            return None

    @property
    def primary_value(self):
        """ The property value on which to merge.
        """
        try:
            return list(self.__node.properties.values())[0].value
        except IndexError:
            return None

    def set(self, *labels, **properties):
        """ Extra labels and properties to apply to the node.

            >>> merge = MergeNode("Person", "name", "Bob").set("Employee", employee_id=1234)

        """
        self.__labels.update(labels)
        self.__properties.update(properties)
        return self

    def with_return(self):
        """ Include a RETURN clause in the statement.
        """
        self.__return = True
        return self

    @property
    def statement(self):
        """ The full Cypher statement.
        """
        string = StringIO()
        writer = CypherWriter(string)
        writer.write_literal("MERGE ")
        writer.write_node(self.__node, "a")
        if self.__labels:
            writer.write_literal(" SET a")
            for label in self.__labels:
                writer.write_label(label)
        if self.__properties:
            writer.write_literal(" SET a={A}")
        if self.__return:
            writer.write_literal(" RETURN a")
        return string.getvalue()

    @property
    def parameters(self):
        """ Dictionary of parameters.
        """
        parameters = {}
        if self.__node.properties:
            parameters["A1"] = self.primary_value
        if self.__properties:
            parameters["A"] = self.properties
        return parameters


class CreateTransaction(object):

    def __init__(self, graph):
        self.graph = graph
        self.cypher = self.graph.cypher
        self.nodes = set()
        self.relationships = set()

    def append(self, entity):
        if isinstance(entity, Node):
            self.nodes.add(entity)
        elif isinstance(entity, Relationship):
            self.relationships.add(entity)
        else:
            raise ValueError("Cannot create an entity of type " + entity.__class__.__name__)

    def create(self):
        tx = self.cypher.begin()

        def create_entities(entities, creator, commit=False):
            if not entities:
                return
            entity_list = list(entities)
            indexes = {}
            creation_index = 0
            for argument_index, entity in enumerate(entity_list):
                if not entity.bound:
                    indexes[creation_index] = argument_index
                    tx.append(creator(entity))
                    creation_index += 1
            results = tx.post(commit)
            for creation_index, result in enumerate(results):
                argument_index = indexes[creation_index]
                entity_list[argument_index].bind(result["data"][0][0]["self"])

        create_entities(self.nodes,
                        lambda n: CreateNode(*n.labels, **n.properties).with_return())
        create_entities(self.relationships,
                        lambda r: CreateRelationship(r.start_node, r.end_node,
                                                     r.type, **r.properties).with_return())

        tx.commit()
