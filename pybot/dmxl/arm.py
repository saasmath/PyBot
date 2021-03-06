#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" A simple model for a serial robotic arm build with Dynamixel servos. """

import time
import math

from . import classes as dmxl
from .classes import Register

logger = None

def pose_distance(p1, p2):
    """ Returns the distance between two poses.

    The distance is defined as the classical euclidian distance, working in a
    space in which a coordinate represent the angle of a joint.

    Parameters:
        p1, p2:
            the poses which distance is computed. They can be either a
            dictionary which keys are the joint ids, and values are the joint
            angles, or the equivalent tuples list.
            Both poses must be related to the same poses space of course

    Returns:
        the euclidian distance between the poses

    Raises:
        ValueError if poses components don't refer to the same poses space
    """

    if type(p1) is not dict:
        p1 = dict(p1)
    if type(p2) is not dict:
        p2 = dict(p2)
    if p1.viewkeys() != p2.viewkeys():
        raise ValueError('poses components mismatch')

    return math.sqrt(sum(d*d for d in [p1[jid] - p2[jid] for jid in p1.iterkeys()]))

class Joint(object):
    """ A joint of the arm.

    Joints are moved by servos of course, and this class acts as the interface
    between the user's vision based in terms of the physical aspects of the joint
    and the corresponding commands and settings of the servo. By instance, when
    dealing with position we talk about angles and not servo steps.
    """
    ORIENTATION_CCW = 1
    ORIENTATION_CW = -1

    _setup_is_valid = False

    def __init__(self, dmxlid, intf,
                 angles_origin=0,
                 angles_orientation=ORIENTATION_CCW,
                 angles_range=(0,300),
                 angles_resolution=300. / 1023,
                 servo_setup=None):
        """ Constructor.

        Has lots of parameters, but common defaults for most of them.

        Parameters:
            dmxlid:
                the id of the joint servo
            intf:
                the Dynamixel interface used to talked to the servos
            angles_origin:
                the servo position corresponding to 0 degrees
                (default : 0)
            angles_orientation:
                the direction in which angles are counted positive
                (default: counter-clockwise, ie trigonometric)
            angles_range:
                a tuple containing the valid range for angles
                (default: (0, 300) which is the max travel for AX12 and alike)
            angles_resolution:
                the conversion ratio between degrees and steps (deg = pos * angles_resolution)
                (default: AX12 value, is 1023 steps for 300 degrees.
            servo_setup:
                a dictionary containing the values to be set to the servo registers.
                Keys are the register names (see dxml_lib.Register class)
                (default: None)

        Raises:
            ValueError if parameters out of range, of if inconsistency in angles related
            settings (origin, orientation and range).
        """
        if not 1 <= dmxlid <= 253:
            raise ValueError('invalid servo id')
        if not intf:
            raise ValueError('intf cannot be None')
        if not isinstance(intf, dmxl.DynamixelBusInterface):
            raise TypeError('intf must be DynamixelBusInterface instance')

        self._dmxlid = dmxlid
        self._intf = intf

        self.angles_origin = angles_origin
        self.angles_orientation = angles_orientation
        self.angles_range = angles_range
        self.angles_resolution = angles_resolution

        self.servo_setup = {
            Register.ReturnDelay : 0,
            Register.TorqueLimit : 800,
            Register.MovingSpeed : 0x3ff
        }

        if servo_setup:
            self.servo_setup.update(servo_setup)

        if self.angles_orientation == Joint.ORIENTATION_CCW:
            self.servo_setup[Register.CWAngleLimit] = self.angle_to_pos(angles_range[0])
            self.servo_setup[Register.CCWAngleLimit] = self.angle_to_pos(angles_range[1])
        else:
            self.servo_setup[Register.CCWAngleLimit] = self.angle_to_pos(angles_range[0])
            self.servo_setup[Register.CWAngleLimit] = self.angle_to_pos(angles_range[1])

        if self.servo_setup[Register.CWAngleLimit] >= self.servo_setup[Register.CCWAngleLimit]:
            msg = 'inconsistency in setup of angles orientation, origin and range'
            if logger:
                logger.error(msg)
            raise ValueError(msg)

        self._setup_is_valid = True
        self.apply_servo_setup()

#        print('*** servo id=%d' % self._dmxlid)
#        print('- setup:')
#        self._dump_setup(self.servo_setup)
#        print(' - registers:')
#        self.dump_regs()

    @property
    def dmxlid(self):
        return self._dmxlid

    @staticmethod
    def _dump_setup(setup):
        for k, v in [(k, setup[k]) for k in sorted(setup.keys())]:
            Register.dump(k, v)

    def dump_regs(self):
        self._intf.dump_regs(self._dmxlid)

    def apply_servo_setup(self):
        if self._setup_is_valid:
            for reg, value in self.servo_setup.iteritems():
                self._intf.write_register(self._dmxlid, reg, value)

        else:
            raise RuntimeError('cannot apply invalid settings')

    def angle_to_pos(self, angle):
        msg = None
        if angle < self.angles_range[0]:
            angle = self.angles_range[0]
            msg = 'angle clamped to low bound'
        elif angle > self.angles_range[1]:
            angle = self.angles_range[1]
            msg = 'angle clamped to high bound'
        if msg and logger:
            logger.warning('[%d] %s (%f)' % (self._dmxlid, msg, angle))

        return max(0,
                   min(1023,
                       int(round(angle / self.angles_resolution * self.angles_orientation)
                           + self.angles_origin)
                       )
                   )

    def pos_to_angle(self, pos):
        return (pos - self.angles_origin) * self.angles_resolution * self.angles_orientation

    def set_goal_angle(self, angle, speed=None, immed=True):
        pos = self.angle_to_pos(angle)
        if speed:
            self._intf.write_register(self._dmxlid, Register.MovingSpeed, value=speed)
        self._intf.write_register(self._dmxlid, Register.GoalPosition, pos, immed)
        return pos

    def get_current_angle(self):
        pos = self._intf.read_register(self._dmxlid, Register.CurrentPosition)
        return self.pos_to_angle(pos)

    def is_moving(self):
        return self._intf.read_register(self._dmxlid, Register.Moving)

    def set_enable(self, state):
        self._intf.write_register(self._dmxlid, Register.TorqueEnable, state)


class DynamixelArm(object):
    """ A serial arm, made of a chain of several joints. """

    def __init__(self, intf, config):
        """ Constructor.

        Parameters:
            intf (dmxl_lib.DynamixelBusInterface):
                a DynamixelBusInterface instance

            config (dict):
                a dictionary containing the configuration of
                the arm

        .. note::
            The keys of the configuration dictionary are the identifiers
            by which the joints are identified by the various methods.
            The associated value are dictionaries containing
            the Joint constructor parameters as kwargs

        Raises:
            ValueError:
                if intf or config is None
            TypeError:
                if config is not a dict
        """

        if not intf:
            raise ValueError("intf parameter cannot be None")
        if not config:
            raise ValueError("config parameter cannot be None")
        if type(config) is not dict:
            raise TypeError('config parameter must be a dict')

        self._intf = intf
        self._joints = {}
        self._max_dist2 = 0

        for joint_id, cfg in config.iteritems():
            if type(cfg) is not dict:
                raise TypeError('joint configuration must be a dict')

            self._joints[joint_id] = Joint(intf=intf, **cfg)

        self.config_changed()

    def config_changed(self):
        """ Updates internal states which depends on the arm configuration.

        Must be called if any change is done at joint level after the arm
        initial creation, such as angle range modification for instance.
        """
        # compute the reference value used when computing the normalized pose
        # distance (see dist_from_pose() method)
        self._max_dist2 = sum(d*d for d in [
            r[0]-r[1] for r in [
                j.angles_range for j in self._joints.itervalues()
                ]
            ]
        )

    def __getitem__(self, joint_id):
        """ Returns a joint given its identifier.

        Parameters:
            joint_id (str):
                the id of the joint

        Raises:
            KeyError if joint does not exist
        """
        return self._joints[joint_id]

    def __delitem__(self, joint_id):
        raise RuntimeError('unsupported operation')

    def __setitem__(self, joint_id, joint):
        raise RuntimeError('unsupported operation')

    def __len__(self):
        return len(self._joints)

    def __iter__(self):
        """ Returns an iterator over the collection of joints."""
        return self._joints.itervalues()

    def __repr__(self):
        return '<%s %s>' % (
                self.__class__.__name__,
                ' '.join(['%s:%.1f' % (joint_id, joint.get_current_angle()) for
                    joint_id, joint in self._joints.iteritems()])
                )

    def set_enable(self, state):
        """Activates or deactivates the joint servos."""
        for joint in self._joints.itervalues():
            joint.set_enable(state)

    def set_joint_angle(self, joint_id, angle, speed=None, wait=False, immed=True):
        """ Sets the target angle of a given joint.

        Parameters:
            joint_id (str):
                the id of the joint

            angle (int):
                the target angle in degrees. Allowed range : [-150, +150]

            speed (int):
                an optional speed used to override the default one used to
                reach the target position. Allowed range : [0, 1023]

            wait (bool):
                an optional boolean (default: False) telling if the function
                must return at once or wait until the position is reached.

            immed (bool):
                if True the movement is executed at once. Otherwise we just
                set the target position and it is up to the caller to trigger
                the execution (synchronized moves use case). Note that the wait
                parameter has no meaning (and is ignored) if the move is
                not immediate.

        Returns:
            the servo position corresponding to the requested angle (see Joint.set_goal_angle())

        Raises:
            KeyError:
                if joint does not exist
            ValueError:
                if parameter(s) out of valid range
        """
        joint = self._joints[joint_id]
        pos = joint.set_goal_angle(angle, speed, immed)

        if immed and wait:
            while joint.is_moving():
                time.sleep(0.05)

        return pos

    def is_joint_moving(self, joint_id):
        """ Tells if a given joint is still moving or not.

        Parameters:
            joint (str):
                the key of the joint

        Raises:
            KeyError if joint does not exist
        """
        return self._joints[joint_id].is_moving()


    def execute(self, gesture):
        """ Executes a sequence of move statements.

        The sequence is a list of statements, which are executed in sequence,
        the complete execution of the current one being waited for before
        processing the next one.

        Each statement is either a move statement, or a set of move statements
        to be executed in parallel.

        A move statement is a tuple composed of :
        - a joint id
        - a target angle
        - an optional move speed

        A given move (or set of simultaneous move) statement(s) in the sequence
        must have been completed before executing the next one.

        Parameters:
            gesture:
                an instance of Gesture describing the move
        """

        use_sync_read = isinstance(self._intf, dmxl.USB2AX)
        all_ids = []

        for stmt in gesture.sequence:
            # will contain the goal positions keyed by the corresponding servo id
            move_data = {}

            if type(stmt) is tuple:
                # single move statement
                dmxlid, goal_pos = self._prepare_move(stmt)
                move_data[dmxlid] = goal_pos

            elif type(stmt) is set:
                # set of simultaneous moves statement
                for inner_stmt in stmt:
                    dmxlid, goal_pos = self._prepare_move(inner_stmt)
                    move_data[dmxlid] = goal_pos
            else:
                raise ValueError('invalid sequence (%s)' % stmt)

            # accumulate ids of servos involved in th whole sequence
            # (we'll use this list for the final torque hold state)
            for _id in [_id for _id in move_data.keys() if _id not in all_ids]:
                all_ids.append(_id)

            # make all pending moves to occur
            self._intf.action()

            # Wait for all the involved joints to complete their move.

            # Note that we don't use the moving status here since it seems to
            # be updated at a pace slower than real time, which leads to delays
            # between successive moves of the sequence
            if use_sync_read:
                dmxlids = move_data.keys()
                goal_positions = [move_data[dmxlid] for dmxlid in dmxlids]
                while True:
                    current_positions = self._intf.sync_read(dmxlids, Register.CurrentPosition)
                    reached = [abs(a-b) <= gesture.tolerance
                            for a,b in zip(goal_positions, current_positions)]
                    if all(reached):
                        break
                    # we could optimize by removing ids of servo having reached
                    # their goal positions, but there are good chances that the
                    # added computational overhead will not be balanced by the
                    # time saved by not querying these servos (apart perhaps
                    # for very crowded servo assemblies, but this has to be
                    # assessed)

            else:
                while move_data:
                    for dmxlid, goal_position in move_data.items():
                        error = abs(goal_position
                                    - self._intf.read_register(dmxlid, Register.CurrentPosition))
                        if error <= gesture.tolerance:
                            del move_data[dmxlid]

        if not gesture.hold_torque:
            #self.set_enable(False)
            for dmxlid in all_ids:
                self._intf.write_register(dmxlid, Register.TorqueEnable, 0)


    def _prepare_move(self, stmt):
        """ Internal method processing the individual moves of a statement.

        Parameters:
            stmt (tuple) :
                the statement to be prepared.
                Items : (joint_id, angle [, move_speed])

        Returns:
            a tuple (id of the joint servo, goal position equivalent to the angle)
        """
        if len(stmt) == 3:
            joint_id, angle, speed = stmt
        else:
            joint_id, angle = stmt
            speed = None

        joint = self._joints[joint_id]
        try:
            pos = joint.set_goal_angle(angle, speed, immed=False)
        except RuntimeError:
            if logger:
                logger.error('set_goal_angle error : joint=%s goal_angle=%.1f' % (joint_id, angle))
            raise

        return joint.dmxlid, pos

    def get_current_pose(self):
        """ Returns the current pose of the arm.

        A pose is list of tuples, containing each a joint id and its position.
        """
        return [(jid, joint.get_current_angle())
                for jid, joint in self._joints.iteritems()]

    def set_pose(self, pose, speed=None, sequential=False):
        """ Set the pose of the arm.

        Joints moves are done in the sequence given by the pose list.

        Parameters:
            pose:
                the arm pose, as a list of tuples, each one composed of a servo id and
                the corresponding target angle
            speed:
                see set_joint_angle
            sequential:
                if True, each move waits for the previous one to complete
                before start (default: False)
        """
        for jid, angle in pose:
            self.set_joint_angle(jid, angle, speed, wait=sequential)

    def dist_from_pose(self, pose):
        """ Returns the normalized distance between the current arm pose and a given one.

        The distance is defined as the Euclidian distance in a space defined a
        coordinate system which components are the joints angle. It is
        normalized by reference to the span of each joint.

        Parameters:
            pose:
                the reference pose

        Returns:
            the normalized distance.
        """
        p1 = dict(self.get_current_pose())
        p2 = dict(pose)
        if p1.viewkeys() != p2.viewkeys():
            raise ValueError('pose definition  mismatch')

        d2 = sum(d*d for d in [p1[jid] - p2[jid] for jid in p1.iterkeys()])
        return d2 / self._max_dist2

    def closest_pose(self, poses):
        """ Returns the index of the pose in the provided list which is the
        closest from the current one.

        Parameters:
            poses:
                a list of pose

        Returns:
            the index of the closest pose
        """
        dists = [self.dist_from_pose(pose) for pose in poses]
        return dists.index(min(dists))

    def move_to_closest_pose(self, poses, **kwargs):
        """ Moves the arm to the closest pose in the list.

        Parameters:
            poses:
                a list of poses
            **kwargs:
                set_pose() arguments

        Returns:
            the index of the pose the arms moved to
        """
        ndx = self.closest_pose(poses)
        self.set_pose(poses[ndx], **kwargs)
        return ndx


class InvalidMove(Exception):
    def __init__(self, from_pose, to_pose):
        super(InvalidMove, self).__init__()
        self.from_pose, self.to_pose = from_pose, to_pose

    def __str__(self):
        return "cannot move from '%s' to '%s'" % (self.from_pose, self.to_pose)

class InvalidPose(Exception):
    def __init__(self, pose):
        super(InvalidPose, self).__init__()
        self.pose = pose

    def __str__(self):
        return 'invalid pose : %s' % (self.pose,)

class Gesture(object):
    def __init__(self, seq, hold_torque=False, tolerance=10):
        """ Constructor

        Parameters:
            seq :
                the sequence of moves

            hold_torque (bool):
                if True, actuators' torque will be maintained when the sequence is
                completed (default: False)

            tolerance (int):
                the absolute value of the error between the target position and
                the current one under which a movement in progress will be considered
                as complete. Don't use 0 here since depending on the compliance
                settings of the servo, there are room for the target position
                not being exactly reached, and thus the move never being considered as
                complete. Side effect : using 1024 (or more) is equivalent to executing
                all the moves at the same time, since they will always be considered as
                complete.
        """
        self.sequence = seq
        self.hold_torque = hold_torque
        self.tolerance = tolerance

class GestureEngine(object):
    """ A kindof state machine managing the possible arm gestures between poses.
    """

    def __init__(self, arm):
        self._gestures = {}
        self._current_pose = None
        self._arm = arm

    def __repr__(self):
        return '<%s pose:%s>' % (self.__class__.__name__, self._current_pose)

    def add_gesture(self, from_pose, to_pose, sequence,
            hold_torque=False,
            tolerance=10):
        """ Defines an arm gesture from a pose to another one.

        Parameters:
            from_pose:
                starting pose
            to_pose:
                ending pose
            sequence:
                sequence of join moves for executing the gesture
            hold_torque:
                tells if joint torque must be held after the gesture completion
            tolerance:
                the clearance to the target joint position
        """
        if from_pose not in self._gestures:
            self._gestures[from_pose] = {}
        self._gestures[from_pose][to_pose] = Gesture(sequence, hold_torque, tolerance)

    def set_gestures(self, gestures):
        self._gestures = gestures
        self._current_pose = None

    def get_current_pose(self):
        """ Returns the current arm pose. """
        return self._current_pose

    def set_current_pose(self, pose_id):
        """ Sets the current pose.

        The pose must be one of those defined by the add_gesture method,
        otherwise an InvalidPose exception is triggered.
        """
        if pose_id in self._gestures:
            self._current_pose = pose_id
        else:
            raise InvalidPose(pose_id)

    def initialize(self):
        """ Defines the initial pose and moves the arm to it.

        Must be overriden by sub-classes
        """
        raise NotImplementedError()

    def move_to(self, pose_id):
        """ Executes a gesture by moving from the current pose to the requested
        one.

        Acceptability of the gesture with respect to the current pose
        of the arm is checked, based on the gestures table derived
        from all the add_gesture calls.

        Parameters:
            pose_id:
                the target pose, which must have been defined previously
                with the add_gesture method.

        Raises:
            InvalidMove if gesture cannot be done
        """
        if not self._current_pose:
            raise RuntimeError('current pose is not defined')

        if pose_id == self._current_pose:
            return

        try:
            gesture = self._gestures[self._current_pose][pose_id]

        except KeyError:
            raise InvalidMove(self._current_pose, pose_id)

        else:
            if logger:
                logger.info("moving from '%s' to '%s'" % (self._current_pose, pose_id))
            self._arm.execute(gesture)
            self._current_pose = pose_id
            if logger:
                logger.info('current pose is now : %s' % self._current_pose)

