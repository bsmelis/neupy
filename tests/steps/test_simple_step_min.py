import numpy as np

from neupy import algorithms, layers

from data import xor_input_train, xor_target_train
from base import BaseTestCase


class LearningRateUpdatesTestCase(BaseTestCase):
    def setUp(self):
        super(LearningRateUpdatesTestCase, self).setUp()
        self.first_step = 0.3
        self.connection = [
            layers.Tanh(2),
            layers.Tanh(3),
            layers.StepOutput(1, output_bounds=(-1, 1))
        ]

    def test_simple_learning_rate_minimization(self):
        network = algorithms.Backpropagation(
            self.connection,
            step=self.first_step,
            epochs_step_minimizator=50,
            optimizations=[algorithms.SimpleStepMinimization]
        )
        network.train(xor_input_train, xor_target_train, epochs=100)
        self.assertEqual(
            network.variables.step.get_value(),
            self.first_step / 3
        )
