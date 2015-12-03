from __future__ import division

import math
import time
from itertools import groupby
from collections import deque

import six
import numpy as np
import matplotlib.pyplot as plt
import theano
import theano.tensor as T

from neupy.utils import (format_data, is_layer_accept_1d_feature, asfloat,
                         AttributeKeyDict, is_list_of_integers)
from neupy.helpers import preformat_value, table
from neupy.core.base import BaseSkeleton
from neupy.core.properties import (Property, FuncProperty, NumberProperty,
                                   BoolProperty, ChoiceProperty)
from neupy.layers import BaseLayer, Output
from neupy.layers.utils import generate_layers
from .errors import mse, binary_crossentropy, categorical_crossentropy
from .utils import (iter_until_converge, shuffle, normalize_error,
                    normalize_error_list, StopNetworkTraining)
from .connections import LayerConnection, NetworkConnectionError


__all__ = ('BaseNetwork', 'ConstructableNetwork')


def show_epoch_summary(network, show_epoch):
    delay_limit = 1  # in seconds
    prev_summary_time = None
    delay_history_length = 10
    terminal_output_delays = deque(maxlen=delay_history_length)

    table_drawer = table.TableDrawer(
        table.Column(name="Epoch #"),
        table.FloatColumn(name="Train err"),
        table.FloatColumn(name="Valid err"),
        table.TimeColumn(name="Time", width=10),
        stdout=network.logs.simple
    )
    table_drawer.start()

    try:
        while True:
            now = time.time()

            if prev_summary_time is not None:
                time_delta = now - prev_summary_time
                terminal_output_delays.append(time_delta)

            table_drawer.row([
                network.epoch,
                network.last_error(),
                network.last_validation_error() or '-',
                network.train_epoch_time
            ])
            prev_summary_time = now

            if len(terminal_output_delays) == delay_history_length:
                prev_summary_time = None
                average_delay = np.mean(terminal_output_delays)

                if average_delay < delay_limit:
                    show_epoch *= math.ceil(delay_limit / average_delay)
                    table_drawer.line()
                    table_drawer.message("Too many outputs in a terminal.")
                    table_drawer.message("Set up logging after each {} epoch"
                                         "".format(show_epoch))
                    table_drawer.line()
                    terminal_output_delays.clear()

            yield show_epoch

    finally:
        table_drawer.finish()
        network.logs.empty()


def shuffle_train_data(input_train, target_train):
    if target_train is None:
        return shuffle(input_train), None
    return shuffle(input_train, target_train)


def show_network_options(network, highlight_options=None):
    """ Display all available parameters options for Neural Network.

    Parameters
    ----------
    network : object
        Neural network instance.
    highlight_options : list
        List of enabled options. In that case all options from that
        list would be marked with a green color.
    """

    available_classes = [cls.__name__ for cls in network.__class__.__mro__]
    logs = network.logs

    if highlight_options is None:
        highlight_options = {}

    def classname_grouper(option):
        classname = option[1].class_name
        class_priority = -available_classes.index(classname)
        return (class_priority, classname)

    # Sort and group options by classes
    grouped_options = groupby(
        sorted(network.options.items(), key=classname_grouper),
        key=classname_grouper
    )

    has_layer_structure = (
        hasattr(network, 'connection') and
        isinstance(network.connection, LayerConnection)
    )
    if has_layer_structure:
        logs.header("Network structure")
        logs.log("LAYERS", network.connection)

    # Just display in terminal all network options.
    logs.header("Network options")
    for (_, clsname), class_options in grouped_options:
        if not class_options:
            # When in some class we remove all available attributes
            # we just skip it.
            continue

        logs.simple("{}:".format(clsname))

        for key, data in sorted(class_options):
            if key in highlight_options:
                logger = logs.log
                value = highlight_options[key]
            else:
                logger = logs.gray_log
                value = data.value

            formated_value = preformat_value(value)
            logger("OPTION", "{} = {}".format(key, formated_value))

        logs.empty()


def parse_show_epoch_property(value, n_epochs):
    if isinstance(value, int):
        return value

    number_end_position = value.index('time')
    # Ignore grammar mistakes like `2 time`, this error could be
    # really annoying
    n_epochs_to_check = int(value[:number_end_position].strip())

    if n_epochs <= n_epochs_to_check:
        return 1

    return int(round(n_epochs / n_epochs_to_check))


class ShowEpochProperty(Property):
    """ Class helps validate specific syntax for `show_epoch`
    property from ``BaseNetwork`` class.
    """
    expected_type = tuple([int] + [six.string_types])

    def validate(self, value):
        if not isinstance(value, six.string_types):
            if value < 1:
                raise ValueError("Property `{}` value should be integer "
                                 "greater than zero or string. See the "
                                 "documentation for more information."
                                 "".format(self.name))
            return

        if 'time' not in value:
            raise ValueError("`{}` value has invalid string format."
                             "".format(self.name))

        valid_endings = ('times', 'time')
        number_end_position = value.index('time')
        number_part = value[:number_end_position].strip()

        if not value.endswith(valid_endings) or not number_part.isdigit():
            valid_endings_formated = ', '.join(valid_endings)
            raise ValueError(
                "Property `{}` in string format should be a positive number "
                "with one of those endings: {}. For example: `10 times`."
                "".format(self.name, valid_endings_formated)
            )

        if int(number_part) < 1:
            raise ValueError("Part that related to the number in `{}` "
                             "property should be an integer greater or "
                             "equal to one.".format(self.name))


class BaseNetwork(BaseSkeleton):
    """ Base class for Neural Network algorithms.

    Parameters
    ----------
    {full_params}

    Methods
    -------
    {plot_errors}
    {last_error}
    """
    step = NumberProperty(default=0.1)

    # Training settings
    show_epoch = ShowEpochProperty(min_size=1, default='10 times')
    shuffle_data = BoolProperty(default=False)

    # Signals
    epoch_end_signal = FuncProperty()
    train_end_signal = FuncProperty()

    def __init__(self, *args, **options):
        self.errors_in = []
        self.errors_out = []

        self.train_epoch_time = None

        super(BaseNetwork, self).__init__(*args, **options)
        self.init_properties()

        if self.verbose:
            show_network_options(self, highlight_options=options)

    def init_properties(self):
        """ Setup default values before populate the options.
        """

    def predict(self, input_data):
        """ Return prediction results for the input data. Output result also
        include postprocessing step related to the final layer that
        transform output to convenient format for end-use.
        """

    def epoch_start_update(self, epoch):
        """ Function would be trigger before run all training procedure
        related to the current epoch.

        Parameters
        ----------
        epoch : int
            Current epoch number.
        """
        self.epoch = epoch

    def _train(self, input_train, target_train=None, input_test=None,
               target_test=None, epochs=100, epsilon=None):
        """ Main method for the Neural Network training.
        """

        # ----------- Pre-format target data ----------- #

        # TODO: This solution looks ugly, should make it in different
        # way later.
        if hasattr(self, 'connection'):
            is_input_feature1d = is_layer_accept_1d_feature(self.input_layer)
            is_target_feature1d = is_layer_accept_1d_feature(self.output_layer)
        else:
            is_input_feature1d = True
            is_target_feature1d = True

        input_train = format_data(input_train, is_input_feature1d)
        target_train = format_data(target_train, is_target_feature1d)

        if input_test is not None:
            input_test = format_data(input_test, is_input_feature1d)

        if target_test is not None:
            target_test = format_data(target_test, is_target_feature1d)

        # ----------- Validate input values ----------- #

        if epsilon is not None and epochs <= 2:
            raise ValueError("Network should train at teast 3 epochs before "
                             "check the difference between errors")

        # ----------- Predefine parameters ----------- #

        show_epoch = self.show_epoch
        logs = self.logs
        compute_error_out = (input_test is not None and
                             target_test is not None)
        last_epoch_shown = 0

        if epsilon is not None:
            iterepochs = iter_until_converge(self, epsilon, max_epochs=epochs)

            if isinstance(show_epoch, six.string_types):
                show_epoch = 100
                logs.warning("Can't use `show_epoch` value in converging "
                             "mode. Set up 100 to `show_epoch` property "
                             "by default.")

        else:
            iterepochs = range(1, epochs + 1)
            show_epoch = parse_show_epoch_property(show_epoch, epochs)

        epoch_summary = show_epoch_summary(self, show_epoch)

        # ----------- Training procedure ----------- #

        logs.header("Start train")
        logs.log("TRAIN", "Train data size: {}".format(input_train.shape[0]))

        if input_test is not None:
            logs.log("TRAIN", "Validation data size: {}"
                              "".format(input_test.shape[0]))

        if epsilon is None:
            logs.log("TRAIN", "Total epochs: {}".format(epochs))
        else:
            logs.log("TRAIN", "Max epochs: {}".format(epochs))

        logs.empty()

        # Optimizations for long loops
        errors = self.errors_in
        errors_out = self.errors_out
        shuffle_data = self.shuffle_data

        if compute_error_out:
            # TODO: Method is undefined. Should fix it later.
            prediction_error = self.prediction_error

        train_epoch = self.train_epoch
        epoch_end_signal = self.epoch_end_signal
        train_end_signal = self.train_end_signal
        epoch_start_update = self.epoch_start_update

        self.input_train = input_train
        self.target_train = target_train

        for epoch in iterepochs:
            epoch_start_time = time.time()
            epoch_start_update(epoch)

            if shuffle_data:
                input_train, target_train = shuffle_train_data(input_train,
                                                               target_train)
                self.input_train = input_train
                self.target_train = target_train

            try:
                error = train_epoch(input_train, target_train)

                if compute_error_out:
                    error_out = prediction_error(input_test, target_test)
                    errors_out.append(error_out)

                errors.append(error)
                self.train_epoch_time = time.time() - epoch_start_time

                if epoch % show_epoch == 0 or epoch == 1:
                    show_epoch = next(epoch_summary)
                    last_epoch_shown = epoch

                if epoch_end_signal is not None:
                    epoch_end_signal(self)

            except StopNetworkTraining as err:
                # TODO: This notification break table view in terminal.
                # Should show it in different way.
                # Maybe I can send it in generator using ``throw`` method
                logs.log("TRAIN", "Epoch #{} stopped. {}"
                                  "".format(epoch, str(err)))
                break

        if epoch != last_epoch_shown:
            next(epoch_summary)

        if train_end_signal is not None:
            train_end_signal(self)

        epoch_summary.close()
        logs.log("TRAIN", "End train")

    # ----------------- Errors ----------------- #

    def last_error(self):
        if self.errors_in:
            return normalize_error(self.errors_in[-1])

    def last_validation_error(self):
        if self.errors_out:
            return normalize_error(self.errors_out[-1])

    def previous_error(self):
        errors_in = self.errors_in
        if len(errors_in) > 2:
            return normalize_error(errors_in[-2])

    def plot_errors(self, logx=False, ax=None, show=True):
        if not self.errors_in:
            return

        if ax is None:
            ax = plt.gca()

        errors_in = normalize_error_list(self.errors_in)
        errors_out = normalize_error_list(self.errors_out)
        errors_range = np.arange(len(errors_in))
        plot_function = ax.semilogx if logx else ax.plot

        line_error_in, = plot_function(errors_range, errors_in)
        title_text = 'Learning error after each epoch'

        if errors_out:
            line_error_out, = plot_function(errors_range, errors_out)
            ax.legend(
                [line_error_in, line_error_out],
                ['Train error', 'Validation error']
            )
            title_text = 'Learning errors after each epoch'

        ax.set_title(title_text)
        ax.set_xlim(0)

        ax.set_ylabel('Error')
        ax.set_xlabel('Epoch')

        if show:
            plt.show()

    # ----------------- Representations ----------------- #

    def class_name(self):
        return self.__class__.__name__

    def __repr__(self):
        classname = self.class_name()
        options_repr = self._repr_options()
        return "{}({})".format(classname, options_repr)


def clean_layers(connection):
    """ Clean layers connections and format transform them into one format.
    Also this function validate layers connections.

    Parameters
    ----------
    connection : list, tuple or object
        Layers connetion in different formats.

    Returns
    -------
    object
        Cleaned layers connection.
    """

    if is_list_of_integers(connection):
        connection = generate_layers(list(connection))

    if isinstance(connection, tuple):
        connection = list(connection)

    islist = isinstance(connection, list)

    if islist and isinstance(connection[0], BaseLayer):
        chain_connection = connection.pop()
        for layer in reversed(connection):
            chain_connection = LayerConnection(layer, chain_connection)
        connection = chain_connection

    elif islist and isinstance(connection[0], LayerConnection):
        pass

    if not isinstance(connection.output_layer, Output):
        raise NetworkConnectionError("Final layer must be Output class "
                                     "instance.")

    return connection


class ConstructableNetwork(BaseNetwork):
    """ Class contains functionality that helps work with network that have
    constructable layers architecture.

    Parameters
    ----------
    {connection}
    {full_params}

    Methods
    -------
    {plot_errors}
    {last_error}
    """

    shared_docs = {"connection": """connection : list, tuple or object
        Network architecture. That variables could be described in
        different ways. The simples one is a list or tuple that contains
        integers. Each integer describe layer input size. For example,
        ``(2, 4, 1)`` means that network will have 3 layers with 2 input
        units, 4 hidden units and 1 output unit. The one limitation of that
        method is that all layers automaticaly would with sigmoid actiavtion
        function. Other way is just a list of ``BaseLayer``` class
        instances. For example: ``[Tanh(2), Relu(4), Output(1)].
        And the most readable one is just layer pipeline
        ``Tanh(2) > Relu(4) > Output(1)``.
    """}

    error = ChoiceProperty(default='mse', choices={
        'mse': mse,
        'binary_crossentropy': binary_crossentropy,
        'categorical_crossentropy': categorical_crossentropy,
    })

    def __init__(self, connection, *args, **kwargs):
        self.connection = clean_layers(connection)

        self.layers = list(self.connection)
        self.input_layer = self.layers[0]
        self.hidden_layers = self.layers[1:-1]
        self.output_layer = self.layers[-1]
        self.train_layers = self.layers[:-1]

        self.variables = AttributeKeyDict()
        self.methods = AttributeKeyDict()

        self.init_layers()
        super(ConstructableNetwork, self).__init__(*args, **kwargs)

        self.init_variables()
        self.init_methods()

    def init_variables(self):
        """ Initialize Theano variables.
        """

        network_input = T.matrix('x')
        network_output = T.matrix('y')

        layer_input = network_input
        for layer in self.train_layers:
            layer_input = layer.output(layer_input)
        prediction = layer_input

        self.variables.update(
            network_input=network_input,
            network_output=network_output,
            step=theano.shared(name='step', value=asfloat(self.step)),
            epoch=theano.shared(name='epoch', value=1, borrow=False),
            error_func=self.error(network_output, prediction),
            prediction_func=prediction,
        )

    def init_methods(self):
        """ Initialize all methods that needed for prediction and
        training procedures.
        """

        network_input = self.variables.network_input
        network_output = self.variables.network_output

        self.methods.train_epoch = theano.function(
            inputs=[network_input, network_output],
            outputs=self.variables.error_func,
            updates=self.init_train_updates(),
        )
        self.methods.prediction_error = theano.function(
            inputs=[network_input, network_output],
            outputs=self.variables.error_func
        )
        self.methods.predict_raw = theano.function(
            inputs=[network_input],
            outputs=self.variables.prediction_func
        )

    def init_layers(self):
        """ Initialize layers in the same order as they were list in
        network initialization step.
        """
        for layer in self.train_layers:
            layer.initialize()

    def init_train_updates(self):
        """ Initialize train function update in Theano format that
        would be trigger after each trainig epoch.
        """
        updates = []
        for layer in self.train_layers:
            updates.extend(self.init_layer_updates(layer))
        return updates

    def init_layer_updates(self, layer):
        """ Initialize train function update in Theano format that
        would be trigger after each trainig epoch for each layer.

        Parameters
        ----------
        layer : object
            Any layer that inherit from BaseLayer class.

        Returns
        -------
        list
            Update that excaptable by ``theano.function``. There should be
            a lits that contains tuples with 2 elements. First one should
            be parameter that would be updated after epoch and the second one
            should update rules for this parameter. For example parameter
            could be a layer's weight and bias.
        """
        updates = []
        for parameter in layer.parameters:
            updates.extend(self.init_param_updates(layer, parameter))
        return updates

    def init_param_updates(self, parameter):
        return []

    def prediction_error(self, input_data, target_data):
        """ Calculate prediction accuracy for input data.
        """
        input_data = format_data(input_data)
        return self.methods.prediction_error(input_data, target_data)

    def predict_raw(self, input_data):
        """ Make raw prediction without final layer postprocessing step.
        """
        input_data = format_data(input_data)
        return self.methods.predict_raw(input_data)

    def predict(self, input_data):
        """ Return prediction results for the input data. Output result also
        include postprocessing step related to the final layer that
        transform output to convenient format for end-use.
        """
        raw_prediction = self.predict_raw(input_data)
        return self.output_layer.output(raw_prediction)

    def epoch_start_update(self, epoch):
        """ Function would be trigger before run all training procedure
        related to the current epoch.

        Parameters
        ----------
        epoch : int
            Current epoch number.
        """
        super(ConstructableNetwork, self).epoch_start_update(epoch)
        self.variables.epoch.set_value(epoch)

    def train_epoch(self, input_train, target_train):
        return self.methods.train_epoch(input_train, target_train)

    def __repr__(self):
        return "{}({}, {})".format(self.class_name(), self.connection,
                                   self._repr_options())
