#!/usr/bin/env python
#
###############################################################################
# Author: Greg Zynda
# Last Modified: 05/16/2019
###############################################################################
# BSD 3-Clause License
# 
# Copyright (c) 2019, Texas Advanced Computing Center
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
###############################################################################

#!pip install hmmlearn &> /dev/null
import numpy as np
import os, psutil
#from hmmlearn import hmm
os.putenv('TF_CPP_MIN_LOG_LEVEL','3')
import tensorflow as tf
tf.logging.set_verbosity(tf.logging.ERROR)
try:
	import horovod.tensorflow as hvd
	#hvd.init()
except:
	hvd = False
from time import time
import logging
logger = logging.getLogger(__name__)

def main():
	from tensorflow.python.client import device_lib
	for d in device_lib.list_local_devices(): print(d.name)
	
	reset_graph()
	# init models
	n_steps = 100
	models = [sleight_model(name="m1", n_steps=n_steps, n_neurons=100, learning_rate=0.001),
		sleight_model(name="m2", n_steps=n_steps, n_neurons=100, learning_rate=0.001),
		sleight_model(name="m3", n_steps=n_steps, n_neurons=20, learning_rate=0.001),
		sleight_model(name="m4", n_steps=n_steps, n_neurons=20, learning_rate=0.001, bidirectional=True)]

	# Training size
	n_iterations = 500
	batch_size = 50

	# Train models
	for i in range(n_iterations):
		# Generate batch data
		x_batch, y_batch = table.batch_roll(batch_size, models[0].n_steps)
		# Train
		mse = [m.train(x_batch, y_batch) for m in models]
		# Print mse
		if (i+1) % 100 == 0:
			outstr = "Iteration %3i"%(i+1)
			for i in range(len(models)):
				outstr += " %s=%.4f"%(models[i].name, mse[i])
			print(outstr)

	# Predict
	x_batch, y_batch = table.batch_roll(1, n_steps)
	pre_list = [m.predict(x_batch, y_batch) for m in models]


	# Delete models
	print(mem_usage())
	del models
	tf.reset_default_graph()
	print(mem_usage())

class sleight_model:
	# https://github.com/tensorflow/models/blob/1af55e018eebce03fb61bba9959a04672536107d/research/autoencoder/autoencoder_models/DenoisingAutoencoder.py
	def __init__(self, name, n_inputs=1, n_steps=50, n_outputs=1, n_neurons=100, n_layers=1, \
		 learning_rate=0.001, training_keep=0.95, dropout=False, \
		 cell_type='rnn', peep=False, stacked=False, bidirectional=False, \
		 reg_losses=False, hidden_list=[], save_dir='.'):
		self.name = name # Name of the model
		self.n_inputs = n_inputs # Number of input features
		self.n_outputs = n_outputs # Number of outputs
		self.n_steps = n_steps # Size of input sequence
		self.n_neurons = n_neurons # Number of neurons per RNN cell
		self.n_layers = n_layers # Number of RNN layers
		self.learning_rate = learning_rate # Learning rate of the optimization algorithm
		self.cell_type = cell_type # Type of RNN cells to use
		# supported cell types
		self.cell_options = {'rnn':tf.contrib.rnn.BasicRNNCell, 'lstm':tf.nn.rnn_cell.LSTMCell}
		self.peep = peep # use peephole connections
		self.stacked = stacked # stacked outputs for efficient computation
		self.dropout = dropout # enable dropout
		self.training_keep = training_keep # keep rate with dropout
		# https://www.tensorflow.org/api_docs/python/tf/nn/bidirectional_dynamic_rnn
		self.bidirectional = bidirectional # each RNN layer is bidirectional
		self.reg_losses = reg_losses # Regularize losses in addition to normal optimization
		self.hidden_list = hidden_list # List of hidden layer sizes
		cell_prefix = 'bi' if bidirectional else ''
		self.param_name = '%s_i%ix%i_%s%s%ix%i_learn%.3f_p%s_s%s_d%.2f_r%s_h%i'%(name, \
			self.n_steps, self.n_inputs, cell_prefix, cell_type, n_layers, \
			n_neurons, learning_rate, str(peep)[0], str(stacked)[0], training_keep, \
			str(reg_losses)[0], len(hidden_list))
		if save_dir[0] == '/':
			self.save_dir = save_dir
		else:
			self.save_dir = os.path.join(os.getcwd(), save_dir)
		self.save_file = os.path.join(self.save_dir, '%s.ckpt'%(self.param_name))
		self.graph = tf.Graph() # Init graph
		logger.debug("Created graph")
		######################################
		# Build graph
		######################################
		with self.graph.as_default():
			# Set the seed before constructing graph
			tf.set_random_seed(seed=42)
			self.X = tf.placeholder(tf.float32, [None, self.n_steps, self.n_inputs])
			self.Y = tf.placeholder(tf.float32, [None, self.n_steps, self.n_outputs])
			self.keep_p = tf.placeholder_with_default(1.0, shape=())
			logger.debug("Created placeholders")
			# Define cells
			if self.bidirectional:
				self.cells_f = self._gen_cells()
				self.multi_layer_cell_f = self._gen_multilayer(self.cells_f)
				self.cells_r = self._gen_cells()
				self.multi_layer_cell_r = self._gen_multilayer(self.cells_r)
				logger.debug("Created bidirectional multi layer cells")
				self.bi_outputs, self.bi_states	= tf.nn.bidirectional_dynamic_rnn(
						cell_fw=self.multi_layer_cell_f,
						cell_bw=self.multi_layer_cell_r,
						dtype=tf.float32, inputs=self.X)
				logger.debug("Created bidirectional dynamic rnn")
				self.outputs_concat = tf.concat(self.bi_outputs, 2)
				self.outputs_reshape = tf.reshape(self.outputs_concat, [-1, 2*self.n_neurons])
				logger.debug("Reshaped outputs")
				self.outputs_combined = tf.contrib.layers.fully_connected(self.outputs_reshape, self.n_outputs, activation_fn=None)
				self.outputs = tf.reshape(self.outputs_combined, [-1, self.n_steps, self.n_outputs])
				logger.debug("Created bidirectional outputs")
			else:
				self.cells = self._gen_cells()
				self.multi_layer_cell = self._gen_multilayer(self.cells)
				logger.debug("Created recurrent layers")
				# Generate outputs
				if self.stacked:
					self.rnn_outputs, self.states = tf.nn.dynamic_rnn(self.multi_layer_cell, self.X, dtype=tf.float32)
					self.stacked_rnn_outputs = tf.reshape(self.rnn_outputs, [-1, self.n_neurons])
					self.stacked_outputs = tf.contrib.layers.fully_connected(self.stacked_rnn_outputs, self.n_outputs, activation_fn=None)
					self.outputs = tf.reshape(self.stacked_outputs, [-1, self.n_steps, self.n_outputs])
					logger.debug("Created stacked output")
				else:
					self.wrapped_cell = tf.contrib.rnn.OutputProjectionWrapper(self.multi_layer_cell, output_size=self.n_outputs)
					self.outputs, self.states = tf.nn.dynamic_rnn(self.wrapped_cell, self.X, dtype=tf.float32)
			if self.hidden_list:
				# Hidden layers
				self.h = tf.contrib.layers.fully_connected(self.outputs, self.hidden_list[0], activation_fn=tf.nn.relu)
				for n_units in self.hidden_list[1:]:
					self.h = tf.contrib.layers.fully_connected(self.h, n_units, activation_fn=tf.nn.relu)
				self.h_last = tf.contrib.layers.fully_connected(self.h, self.n_outputs, activation_fn=None)
				self.logits = tf.reshape(self.h_last, [-1, self.n_steps, self.n_outputs])
			else:
				self.logits = self.outputs
			# Optimize
			if self.reg_losses:
				self.rec_loss = tf.reduce_mean(tf.square(self.logits - self.Y))
				self.reg_loss = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
				self.loss = tf.add_n([self.rec_loss] + self.reg_loss)
			else:
				self.loss = tf.reduce_mean(tf.square(self.logits - self.Y))
			if hvd:
				self.optimizer = tf.train.AdamOptimizer(\
					learning_rate=self.learning_rate*hvd.size())
				self.optimizer = hvd.DistributedOptimizer(self.optimizer)
			else:
				self.optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate)
			self.training_op = self.optimizer.minimize(self.loss)
			# Init sess
			logger.debug("Initializing session")
			self.saver = tf.train.Saver()
			init = tf.initializers.global_variables()
			if hvd:
				self.bcast = hvd.broadcast_global_variables(0)
			tacc_nodes = {'knl':(136,2), 'skx':(48,2)}
			#tacc_nodes = {'knl':(8,1), 'skx':(48,2)}
			if os.getenv('TACC_NODE_TYPE', False) in tacc_nodes:
				logger.debug("Using config for TACC %s node"%(os.getenv('TACC_NODE_TYPE')))
				intra, inter = tacc_nodes[os.getenv('TACC_NODE_TYPE')]
				os.putenv('KMP_BLOCKTIME', '1')
				os.putenv('KMP_AFFINITY', 'granularity=fine,noverbose,compact,1,0')
				os.putenv('OMP_NUM_THREADS', str(intra))
				config = tf.ConfigProto(intra_op_parallelism_threads=intra, \
						inter_op_parallelism_threads=inter, \
						allow_soft_placement=True, \
						device_count = {'CPU': intra})
				self.sess = tf.Session(config=config)
			else:
				logger.debug("Using default config")
				self.sess = tf.Session()
			if hvd:
				self.sess.run(init)
				logger.debug("Broadcasting variables")
				self.sess.run(self.bcast)
			else:
				self.sess.run(init)
	def _gen_multilayer(self, cell_list):
		if self.dropout:
			return tf.contrib.rnn.MultiRNNCell([tf.contrib.rnn.DropoutWrapper(cell, input_keep_prob=self.keep_p) for cell in cell_list])
		else:
			return tf.contrib.rnn.MultiRNNCell(cell_list)
	def _gen_cells(self):
		cell_func = self.cell_options[self.cell_type]
		if self.cell_type == 'lstm' and self.peep:
			return [cell_func(num_units=self.n_neurons, use_peepholes=True) for i in range(self.n_layers)]
		else:
			return [cell_func(num_units=self.n_neurons) for i in range(self.n_layers)]
	def __del__(self):
		logger.debug("Closing session")
		self.sess.close()
		logger.debug("Session closed")
	def save(self):
		if not hvd or (hvd and hvd.rank() == 0):
			with self.graph.as_default():
				if not os.path.exists(self.save_dir):
					os.makedirs(self.save_dir)
				self.saver.save(self.sess, self.save_file)
	def restore(self):
		with self.graph.as_default():
			self.saver.restore(self.sess, self.save_file)
			self.sess.run(self.bcast)
			logger.debug("Restored model from %s"%(self.save_file))
	def train(self, x_batch, y_batch):
		with self.graph.as_default():
			start_time = time()
			#self.sess.run(self.bcast)
			opt_ret, mse = self.sess.run([self.training_op, self.loss], \
				feed_dict={self.X:x_batch, self.Y:y_batch, \
						self.keep_p:self.training_keep})
			end_time = time()
			total_time = end_time - start_time
			logger.debug("Finished training batch in %.1f seconds (%.1f sequences/second)"%(total_time, len(x_batch)/total_time))
		return (mse, total_time)
	def predict(self, x_batch, render=False):
		with self.graph.as_default():
			# The shape is now probably wrong for this
			y_pred = np.abs(self.sess.run(self.logits, \
					feed_dict={self.X:x_batch, self.keep_p:1.0}).round(0))
			y_pred = y_pred.astype(np.uint8)
		if render:
			print("Model: %s"%(self.name))
			# render results
			#renderResults(x_batch.flatten(), y_batch.flatten(), y_pred)
		return y_pred

def mem_usage():
	process = psutil.Process(os.getpid())
	return process.memory_info().rss/1000000

def reset_graph(seed=42):
	tf.reset_default_graph()
	tf.set_random_seed(seed)
	np.random.seed(seed)

if __name__ == "__main__":
	main()
